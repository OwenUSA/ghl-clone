/**
 * Hand the browser a file.
 *
 * Kept out of `lib/csv.ts` because node executes that module directly in
 * `backend/tests/test_csv_export.py`, and out of the components because more than
 * one screen exports (the Bulk Actions selection, and the ⋯ menu's Export of the
 * whole filtered set).
 */
export function downloadCsv(filename: string, text: string): void {
  // The BOM is what makes Excel read the file as UTF-8 rather than as the local
  // codepage, which mangles every accented contact name.
  const blob = new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
