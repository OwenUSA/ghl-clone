// The ONE place a SIP.js user agent is constructed.
//
// A seam, and nothing more: `lib/softphone.ts` asks for a SimpleUser here instead of
// calling `new Web.SimpleUser` itself. That lets the headless-browser check of the dialer
// and in-call window (`backend/tests/browser_dialer.py`) swap in a fake SIP user at the
// dev server — `frontend/e2e/vite.sipmock.config.ts` redirects this module — so the
// real hook, the real components and the real API run while no WebSocket, no
// registration and no call can reach a phone system. The production build never
// references the fake: nothing in `src/` imports it.
import { Web } from 'sip.js'

export type SipUser = Web.SimpleUser
export type SipUserOptions = Web.SimpleUserOptions

export function createSipUser(server: string, options: SipUserOptions): SipUser {
  return new Web.SimpleUser(server, options)
}
