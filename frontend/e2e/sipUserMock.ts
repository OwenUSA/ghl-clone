// A FAKE SIP user, for the headless-browser check of the dialer and in-call window only.
//
// `vite.sipmock.config.ts` puts this module where `src/lib/sipUser.ts` would be, so the
// real softphone hook, the real components and the real CRM API run while nothing here
// opens a WebSocket, registers or places a call. Nothing in `src/` imports this file, so a
// production build (`npm run build`, plain `vite.config.ts`) cannot contain it.
//
// The check drives it through `window.__sipMock`:
//   invite(number, dialed?)  an INVITE arrives, caller-ID `number`
//   remoteHangup()           the far end hangs up
//   log                      every call the hook made: answer, decline, hangup, mute,
//                            unmute, dtmf:<tone>, register ...
type Delegate = {
  onRegistered?: () => void
  onUnregistered?: () => void
  onServerConnect?: () => void
  onServerDisconnect?: (e?: Error) => void
  onCallReceived?: () => void
  onCallAnswered?: () => void
  onCallHangup?: () => void
}

type Options = { delegate?: Delegate; media?: { constraints?: { audio?: unknown } } }

type Mock = {
  log: string[]
  constraints: unknown[]
  invite: (number: string, dialed?: string) => void
  remoteHangup: () => void
  failNextAnswer: boolean
  current: FakeSimpleUser | null
}

declare global {
  interface Window { __sipMock?: Mock }
}

function mock(): Mock {
  if (!window.__sipMock) {
    window.__sipMock = {
      log: [], constraints: [], failNextAnswer: false, current: null,
      invite: (number, dialed) => window.__sipMock?.current?.receive(number, dialed ?? ''),
      remoteHangup: () => window.__sipMock?.current?.end('remote-hangup'),
    }
  }
  return window.__sipMock
}

class FakeSimpleUser {
  session: { remoteIdentity: { uri: { user: string }; displayName: string } } | undefined
  private readonly delegate: Delegate
  private readonly options: Options

  constructor(_server: string, options: Options) {
    this.options = options
    this.delegate = options.delegate ?? {}
    mock().current = this
  }

  private note(what: string) {
    mock().log.push(what)
  }

  async connect() { this.note('connect'); this.delegate.onServerConnect?.() }
  async register() {
    this.note('register')
    setTimeout(() => this.delegate.onRegistered?.(), 50)
  }
  async unregister() { this.note('unregister') }
  async disconnect() { this.note('disconnect') }

  receive(number: string, dialed: string) {
    if (this.session) {
      // SIP.js SimpleUser (maxSimultaneousSessions 1) rejects a second INVITE with 486.
      this.note(`busy:${number}`)
      return
    }
    this.note(`invite:${number}`)
    this.session = { remoteIdentity: { uri: { user: number }, displayName: dialed } }
    this.delegate.onCallReceived?.()
  }

  async answer() {
    mock().constraints.push(this.options.media?.constraints?.audio ?? null)
    if (mock().failNextAnswer) {
      mock().failNextAnswer = false
      this.note('answer-failed')
      this.session = undefined
      throw new Error('Permission denied')
    }
    this.note('answer')
    setTimeout(() => this.delegate.onCallAnswered?.(), 30)
  }

  async decline() { this.note('decline'); this.end(null) }
  async hangup() { this.note('hangup'); this.end(null) }
  mute() { this.note('mute') }
  unmute() { this.note('unmute') }
  async sendDTMF(tone: string) { this.note(`dtmf:${tone}`) }

  end(why: string | null) {
    if (why) this.note(why)
    if (!this.session) return
    this.session = undefined
    this.delegate.onCallHangup?.()
  }
}

export type SipUser = FakeSimpleUser
export type SipUserOptions = Options

export function createSipUser(server: string, options: Options): FakeSimpleUser {
  return new FakeSimpleUser(server, options)
}
