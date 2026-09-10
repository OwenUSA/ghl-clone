/**
 * A request from the ctrl+K palette that a page open one record.
 *
 * There is no router yet (DECISIONS.md), so a search result cannot be a URL. The
 * palette instead hands the shell a view and an id, and the shell passes it to
 * the page, which opens its own detail panel — the same panel a click on a row
 * opens, not a second implementation.
 *
 * `n` is a monotonic counter, not decoration. Without it, asking for the SAME
 * record twice produces an identical prop and the effect that reacts to it never
 * fires, so closing a panel and searching for it again would do nothing.
 */
export type Focus = { id: number; n: number }
