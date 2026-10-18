// Flow 6 — In-memory scenario library. A scenario is only usable for regression once an
// engineer approves it (draft -> in_library).

import type { Scenario } from '#/sim/records'

export type LibraryStatus = 'draft' | 'in_library'
export type LibraryEntry = { spec: Scenario; status: LibraryStatus }

export class ScenarioLibrary {
  private readonly entries = new Map<string, LibraryEntry>()

  add(spec: Scenario): LibraryEntry {
    const entry: LibraryEntry = { spec, status: 'draft' }
    this.entries.set(spec.id, entry)
    return entry
  }

  list(): LibraryEntry[] {
    return [...this.entries.values()]
  }

  get(id: string): LibraryEntry | undefined {
    return this.entries.get(id)
  }

  approve(id: string): boolean {
    const entry = this.entries.get(id)
    if (!entry) return false
    entry.status = 'in_library'
    return true
  }

  /** Only approved scenarios may be used by regression. */
  usable(): Scenario[] {
    return [...this.entries.values()].filter((e) => e.status === 'in_library').map((e) => e.spec)
  }
}
