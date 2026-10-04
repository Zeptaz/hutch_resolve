import { expect, test } from '@playwright/test'

test('spoken confirmation keeps the offer until Resolve clears or replaces it', async ({ page }) => {
  await page.goto('/')
  const result = await page.evaluate(async () => {
    const { offerAfterVoiceResult, reconcileVoiceOffer } = await import('/src/voice/offerState.ts')
    const proposal = {
      id: 'offer-1', proposal_hash: 'hash-1', action_type: 'CREATE_REVIEW_TICKET',
      target_label: 'Review this charge', consequences: 'A person reviews the case.',
      expires_at: '2030-01-01T00:00:00Z', package_terms: null,
    }
    const current = { data: proposal, sending: false, error: null, retry: null }
    const afterSpokenYes = offerAfterVoiceResult(current, null)
    const stillPending = reconcileVoiceOffer(afterSpokenYes, { ...proposal, case_id: 'case-1' })
    const replaced = reconcileVoiceOffer(stillPending, { ...proposal, id: 'offer-2', proposal_hash: 'hash-2' })
    const closed = reconcileVoiceOffer(stillPending, null)
    return {
      spokenYesKeepsOffer: afterSpokenYes === current,
      canonicalPendingKeepsOffer: stillPending === current,
      replacementId: replaced?.data.id,
      canonicalClosureRemovesOffer: closed === null,
    }
  })
  expect(result).toEqual({
    spokenYesKeepsOffer: true, canonicalPendingKeepsOffer: true,
    replacementId: 'offer-2', canonicalClosureRemovesOffer: true,
  })
})

test('caller can interrupt a playing reply and keep sending microphone audio', async ({ page }) => {
  await page.goto('/')
  const result = await page.evaluate(async () => {
    const { VoiceCall } = await import('/src/voice/call.ts')
    const call = new VoiceCall('synthetic-conversation') as unknown as Record<string, any>
    const sent: (string | ArrayBuffer)[] = []
    let flushed = 0
    const audio = {
      onFrame: (_pcm: ArrayBuffer, _level: number) => {},
      flush: () => { flushed++ },
      close: () => {},
      micLevel: 0,
      speakerLevel: 0,
    }
    call.audio = audio
    call.socket = { readyState: WebSocket.OPEN, send: (value: string | ArrayBuffer) => sent.push(value) }
    call.state = { ...call.state, phase: 'live', activity: 'speaking' }
    call.reply = { id: 'reply-1', audioEnded: false, playbackStarted: true, playbackDrained: false }
    call.handleFrame(new ArrayBuffer(3200), 0.05)
    call.handleFrame(new ArrayBuffer(3200), 0.05)
    call.handleFrame(new ArrayBuffer(3200), 0.05)
    for (let i = 0; i < 7; i++) call.handleFrame(new ArrayBuffer(3200), 0)
    const controls = sent.filter((item): item is string => typeof item === 'string').map((item) => JSON.parse(item))
    return { flushed, controls, audioFrames: sent.filter((item) => item instanceof ArrayBuffer).length,
      currentReply: call.reply, activity: call.state.activity }
  })
  expect(result).toEqual({
    flushed: 1,
    controls: [{ type: 'input_activity_start', segment_id: 1 }, { type: 'input_activity_end', segment_id: 1 }],
    audioFrames: 10,
    currentReply: null,
    activity: 'listening',
  })
})

test('browser speech detector rejects background/echo levels and requires sustained caller speech', async ({ page }) => {
  await page.goto('/')
  const result = await page.evaluate(async () => {
    const { SpeechActivityDetector } = await import('/src/voice/call.ts')
    const detector = new SpeechActivityDetector()
    const noise = Array.from({ length: 30 }, () => detector.observe(0.02, true))
    const voice = [0.07, 0.07, 0.07].map((level) => detector.observe(level, true))
    const silence = Array.from({ length: 7 }, () => detector.observe(0.004, true))
    const listening = new SpeechActivityDetector()
    const quietCaller = [0.02, 0.02, 0.02].map((level) => listening.observe(level, false))
    return { noiseStart: noise.includes('start'), voiceStart: voice.at(-1), voiceEnd: silence.at(-1),
      quietCallerStart: quietCaller.at(-1) }
  })
  expect(result).toEqual({ noiseStart: false, voiceStart: 'start', voiceEnd: 'end', quietCallerStart: 'start' })
})

test('playback status remains speaking across streamed PCM gaps until audio_end drains', async ({ page }) => {
  await page.goto('/')
  const result = await page.evaluate(async () => {
    const { playbackActivity } = await import('/src/voice/call.ts')
    return {
      duringGap: playbackActivity(false, false),
      onFrame: playbackActivity(true, false),
      afterDrain: playbackActivity(false, true),
    }
  })
  expect(result).toEqual({ duringGap: null, onFrame: 'speaking', afterDrain: 'listening' })
})

test('echo protection lasts until local playback drains, not just provider audio_end', async ({ page }) => {
  await page.goto('/')
  const result = await page.evaluate(async () => {
    const { VoiceCall } = await import('/src/voice/call.ts')
    function harness() {
      const call = new VoiceCall('synthetic-conversation') as unknown as Record<string, any>
      const controls: Record<string, unknown>[] = []
      const audio = {
        flush: () => {}, beginReply: () => {}, endReply: (_id: string) => {},
        onPlayingChange: (playing: boolean) => call.handlePlaybackChange(playing),
        onDrained: (id: string) => call.drained(id),
      }
      call.audio = audio
      call.socket = { readyState: WebSocket.OPEN, send: (value: string | ArrayBuffer) => {
        if (typeof value === 'string') controls.push(JSON.parse(value))
      } }
      call.state = { ...call.state, phase: 'live' }
      call.handle({ type: 'resolve_result', response_id: 'reply-1', proposal: null })
      return { call, audio, controls }
    }
    const pending = harness()
    for (let i = 0; i < 3; i++) pending.call.handleFrame(new ArrayBuffer(3200), 0.02)
    const active = harness()
    active.call.handle({ type: 'audio_start', response_id: 'reply-1' })
    active.audio.onPlayingChange(true)
    active.call.handle({ type: 'audio_end', response_id: 'reply-1' })
    // Provider generation finished, but scheduled PCM is still audible.
    for (let i = 0; i < 30; i++) active.call.handleFrame(new ArrayBuffer(3200), 0.025)
    const interruptedBeforeDrain = active.controls.some((control) => control.type === 'input_activity_start')
    active.audio.onPlayingChange(false)
    active.audio.onDrained('reply-1')
    for (let i = 0; i < 3; i++) active.call.handleFrame(new ArrayBuffer(3200), 0.02)
    return {
      normalSpeechBeforePlayback: pending.controls.some((control) => control.type === 'input_activity_start'),
      interruptedBeforeDrain,
      normalSpeechAfterDrain: active.controls.some((control) => control.type === 'input_activity_start'),
    }
  })
  expect(result).toEqual({ normalSpeechBeforePlayback: true, interruptedBeforeDrain: false, normalSpeechAfterDrain: true })
})

test('expired grant retries rotate the key while unknown outcomes retain it', async ({ page }) => {
  await page.goto('/chat')
  const result = await page.evaluate(async () => {
    const { grantKeyAfterFailure } = await import('/src/voice/call.ts')
    const key = 'first-attempt-key'
    return {
      expired: grantKeyAfterFailure(key, Object.assign(new Error('expired'), { code: 'VOICE_GRANT_EXPIRED' })),
      unknown: grantKeyAfterFailure(key, Object.assign(new Error('unknown'), { code: 'VOICE_GRANT_OUTCOME_UNKNOWN' })),
      network: grantKeyAfterFailure(key, Object.assign(new Error('network'), { code: 'NETWORK_ERROR' })),
    }
  })
  expect(result).toEqual({ expired: null, unknown: 'first-attempt-key', network: 'first-attempt-key' })
})

test('a stale call attempt cannot claim ownership of the current audio', async ({ page }) => {
  await page.goto('/chat')
  const result = await page.evaluate(async () => {
    const { isCurrentCallAttempt } = await import('/src/voice/call.ts')
    const olderAttempt = {}
    const currentAttempt = {}
    return {
      same: isCurrentCallAttempt(currentAttempt, currentAttempt),
      stale: isCurrentCallAttempt(currentAttempt, olderAttempt),
      ended: isCurrentCallAttempt(null, olderAttempt),
    }
  })
  expect(result).toEqual({ same: true, stale: false, ended: false })
})

test('a permission result arriving after close stops its microphone tracks', async ({ page }) => {
  await page.goto('/chat')
  const result = await page.evaluate(async () => {
    let resolvePermission!: (stream: MediaStream) => void
    let stopped = 0
    class Node {
      gain = { value: 1 }
      fftSize = 256
      port = { onmessage: null as ((event: MessageEvent) => void) | null, close() {} }
      connect() { return this }
      disconnect() {}
      getByteTimeDomainData(data: Uint8Array) { data.fill(128) }
    }
    class FakeAudioContext {
      destination = new Node()
      state = 'running'
      currentTime = 0
      createGain() { return new Node() }
      createAnalyser() { return new Node() }
      createMediaStreamSource() { return new Node() }
      resume() { return Promise.resolve() }
      close() { this.state = 'closed'; return Promise.resolve() }
    }
    Object.defineProperty(FakeAudioContext.prototype, 'audioWorklet', { value: { addModule: async () => {} }, configurable: true })
    Object.defineProperty(window, 'AudioContext', { value: FakeAudioContext, configurable: true })
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getUserMedia: () => new Promise<MediaStream>((resolve) => { resolvePermission = resolve }),
    } })
    const { CallAudio } = await import('/src/voice/audio.ts')
    const audio = new CallAudio()
    const starting = audio.startMic()
    audio.close()
    const stream = { getTracks: () => [{ stop: () => { stopped++ } }] } as unknown as MediaStream
    resolvePermission(stream)
    await starting
    audio.close()
    return { stopped }
  })
  expect(result.stopped).toBe(1)
})

test('microphone tracks are released when audio worklet initialization fails', async ({ page }) => {
  await page.goto('/chat')
  const result = await page.evaluate(async () => {
    let stopped = 0
    class Node {
      gain = { value: 1 }
      fftSize = 256
      port = { onmessage: null as ((event: MessageEvent) => void) | null, close() {} }
      connect() { return this }
      disconnect() {}
      getByteTimeDomainData(data: Uint8Array) { data.fill(128) }
    }
    class FakeAudioContext {
      destination = new Node()
      state = 'running'
      currentTime = 0
      createGain() { return new Node() }
      createAnalyser() { return new Node() }
      createMediaStreamSource() { return new Node() }
      resume() { return Promise.resolve() }
      close() { this.state = 'closed'; return Promise.resolve() }
    }
    Object.defineProperty(FakeAudioContext.prototype, 'audioWorklet', {
      value: { addModule: async () => { throw new Error('worklet unavailable') } }, configurable: true,
    })
    Object.defineProperty(window, 'AudioContext', { value: FakeAudioContext, configurable: true })
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getUserMedia: async () => ({ getTracks: () => [{ stop: () => { stopped++ } }] }),
    } })
    const { CallAudio } = await import('/src/voice/audio.ts')
    const audio = new CallAudio()
    await audio.startMic().catch(() => {})
    audio.close()
    audio.close()
    return { stopped }
  })
  expect(result.stopped).toBe(1)
})
