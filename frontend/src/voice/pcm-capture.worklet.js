// Microphone -> mono PCM16 at 16 kHz in 100 ms frames (3200 bytes, well under Voice's 16 KiB frame limit).
// Downsamples by averaging each window of input samples, which also acts as a simple low-pass filter.
const TARGET_RATE = 16000
const FRAME_SAMPLES = 1600

class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.step = sampleRate / TARGET_RATE
    this.phase = 0
    this.acc = 0
    this.accN = 0
    this.frame = new Int16Array(FRAME_SAMPLES)
    this.n = 0
    this.energy = 0
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel) return true
    for (let i = 0; i < channel.length; i++) {
      this.acc += channel[i]
      this.accN++
      this.phase += 1
      if (this.phase >= this.step) {
        this.phase -= this.step
        const v = Math.max(-1, Math.min(1, this.acc / this.accN))
        this.acc = 0
        this.accN = 0
        this.energy += v * v
        this.frame[this.n++] = v < 0 ? v * 0x8000 : v * 0x7fff
        if (this.n === FRAME_SAMPLES) {
          const level = Math.sqrt(this.energy / FRAME_SAMPLES)
          this.port.postMessage({ pcm: this.frame.buffer, level }, [this.frame.buffer])
          this.frame = new Int16Array(FRAME_SAMPLES)
          this.n = 0
          this.energy = 0
        }
      }
    }
    return true
  }
}

registerProcessor('pcm-capture', PcmCapture)
