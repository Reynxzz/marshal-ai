// Event stream emitted by the simulation engine (pure data, no I/O).

export type Telemetry = Record<string, unknown>

export type SimEvent =
  | { type: 'Arrived'; vin: string; location: string }
  | { type: 'MissionDone'; vin: string; missionId: string }
  | { type: 'TrackStep'; vin: string; step: number; name: string; telemetry: Telemetry }
  | { type: 'RoutineDone'; vin: string }
  | {
      type: 'Status'
      vin: string
      location: string
      x: number
      y: number
      speedKmh: number
      missionId?: string
      progress: number
    }

export type SimEventType = SimEvent['type']
