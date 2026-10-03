import { expect, test } from '@playwright/test'

test('a permission result arriving after close stops its microphone tracks', async ({ page }) => {
  await page.goto('/')
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
  await page.goto('/')
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
