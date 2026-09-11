// ONE softphone for the whole app.
//
// A registration is a scarce thing -- the operator AOR holds a single contact -- so the
// hook must be instantiated exactly once and lifted to the app shell. Mounting it per
// page would re-register on every navigation, and an incoming call would only appear on
// whichever page happened to be open.
import { createContext, useContext, type ReactNode } from 'react'
import { useSoftphone, type SoftphoneApi } from './softphone'

const SoftphoneContext = createContext<SoftphoneApi | null>(null)

export function SoftphoneProvider({ children }: { children: ReactNode }) {
  const softphone = useSoftphone()
  return <SoftphoneContext.Provider value={softphone}>{children}</SoftphoneContext.Provider>
}

export function useSoftphoneContext(): SoftphoneApi {
  const ctx = useContext(SoftphoneContext)
  if (!ctx) throw new Error('useSoftphoneContext must be used inside a SoftphoneProvider')
  return ctx
}
