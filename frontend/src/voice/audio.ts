import captureWorkletUrl from './pcm-capture.worklet.js?url'

const OUTPUT_RATE = 24000

export type MicErrorKind = 'denied' | 'missing' | 'unsupported'

export class MicError extends Error {
  readonly kind: MicErrorKind
  constructor(kind: MicErrorKind) {
    super(kind)
    this.kind = kind
  }
}

/**
 * One AudioContext per call, created inside the "Start call" click so browsers allow playback.
 * Capture: microphone -> worklet -> 16 kHz PCM16 frames. Playback: 24 kHz PCM16 chunks scheduled
 * back to back, grouped by Voice response ID so a reply's end can be detected after it fully drains.
 */
export class CallAudio {
  readonly ctx: AudioContext
  private stream: MediaStream | null = null
  private capture: AudioWorkletNode | null = null
  private outputGain: GainNode
  private outputAnalyser: AnalyserNode
  private analyserData: Uint8Array<ArrayBuffer>
  private inputLevel = 0

  private nextTime = 0
  private sources = new Set<AudioBufferSourceNode>()
  private current: { id: string; pending: number; ended: boolean } | null = null
  private carry: Uint8Array | null = null

  /** Called with each 100 ms microphone frame. */
  onFrame: (pcm: ArrayBuffer) => void = () => {}
  /** Called once a reply's audio has been fully played (after audio_end). */
  onDrained: (responseId: string) => void = () => {}
  /** Called when playback starts or stops, for the speaking indicator. */
  onPlayingChange: (playing: boolean) => void = () => {}

  constructor() {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctx || !('audioWorklet' in Ctx.prototype)) throw new MicError('unsupported')
    this.ctx = new Ctx()
    this.outputGain = this.ctx.createGain()
    this.outputAnalyser = this.ctx.createAnalyser()
    this.outputAnalyser.fftSize = 256
    this.analyserData = new Uint8Array(new ArrayBuffer(this.outputAnalyser.fftSize))
    this.outputGain.connect(this.outputAnalyser).connect(this.ctx.destination)
    void this.ctx.resume()
  }

  async startMic() {
    if (!navigator.mediaDevices?.getUserMedia) throw new MicError('unsupported')
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
    } catch (e) {
      const name = (e as DOMException).name
      throw new MicError(name === 'NotFoundError' || name === 'OverconstrainedError' ? 'missing' : 'denied')
    }
    await this.ctx.audioWorklet.addModule(captureWorkletUrl)
    const source = this.ctx.createMediaStreamSource(this.stream)
    this.capture = new AudioWorkletNode(this.ctx, 'pcm-capture')
    this.capture.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
      this.inputLevel = e.data.level
      this.onFrame(e.data.pcm)
    }
    // The worklet must be pulled by the graph to run; a muted gain keeps the mic out of the speakers.
    const sink = this.ctx.createGain()
    sink.gain.value = 0
    source.connect(this.capture).connect(sink).connect(this.ctx.destination)
  }

  /** Microphone level 0..1, eased for display. */
  get micLevel() {
    return Math.min(1, this.inputLevel * 6)
  }

  /** Playback level 0..1. */
  get speakerLevel() {
    if (this.sources.size === 0) return 0
    this.outputAnalyser.getByteTimeDomainData(this.analyserData)
    let sum = 0
    for (const v of this.analyserData) sum += ((v - 128) / 128) ** 2
    return Math.min(1, Math.sqrt(sum / this.analyserData.length) * 5)
  }

  beginReply(id: string) {
    if (this.current?.id !== id) this.current = { id, pending: 0, ended: false }
  }

  /** Queue one binary frame of 24 kHz PCM16 for the reply that is playing. */
  play(chunk: ArrayBuffer) {
    const reply = this.current
    if (!reply) return
    let bytes = new Uint8Array(chunk)
    if (this.carry) {
      const joined = new Uint8Array(this.carry.length + bytes.length)
      joined.set(this.carry)
      joined.set(bytes, this.carry.length)
      bytes = joined
      this.carry = null
    }
    if (bytes.length % 2) {
      this.carry = bytes.slice(-1)
      bytes = bytes.slice(0, -1)
    }
    const samples = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.length / 2)
    if (!samples.length) return
    const buffer = this.ctx.createBuffer(1, samples.length, OUTPUT_RATE)
    const data = buffer.getChannelData(0)
    for (let i = 0; i < samples.length; i++) data[i] = samples[i] / 0x8000

    const node = this.ctx.createBufferSource()
    node.buffer = buffer
    node.connect(this.outputGain)
    const start = Math.max(this.ctx.currentTime + 0.03, this.nextTime)
    node.start(start)
    this.nextTime = start + buffer.duration
    if (this.sources.size === 0) this.onPlayingChange(true)
    this.sources.add(node)
    reply.pending++
    node.onended = () => {
      this.sources.delete(node)
      if (this.sources.size === 0) this.onPlayingChange(false)
      if (this.current !== reply) return
      reply.pending--
      this.maybeDrained()
    }
  }

  /** audio_end: the reply is complete once everything already queued has played. */
  endReply(id: string) {
    if (this.current?.id !== id) return
    this.current.ended = true
    this.maybeDrained()
  }

  private maybeDrained() {
    const reply = this.current
    if (reply && reply.ended && reply.pending === 0) {
      this.current = null
      this.onDrained(reply.id)
    }
  }

  /** Interruption: drop everything queued and forget the reply, so no acknowledgement is sent. */
  flush() {
    this.current = null
    this.carry = null
    for (const node of this.sources) {
      node.onended = null
      try {
        node.stop()
      } catch {
        /* already stopped */
      }
    }
    if (this.sources.size) this.onPlayingChange(false)
    this.sources.clear()
    this.nextTime = 0
  }

  close() {
    this.flush()
    this.capture?.port.close()
    this.stream?.getTracks().forEach((t) => t.stop())
    void this.ctx.close()
  }
}
