/** Shapes of the §13.2 frame. Derived from the recorded fixture, not from the
 *  spec prose — where the two disagree the fixture wins (e.g. `lanes` is a
 *  dict keyed by lane_id, not an array). */

export type JunctionId = 'J1' | 'J2' | 'J3';
export type Approach = 'north' | 'south' | 'east' | 'west';
export type VehicleType = 'bike' | 'auto' | 'car' | 'truck' | 'ambulance';

export type TypeComposition = Record<VehicleType, number>;

export interface Lane {
  lane_id: string;
  approach: Approach;
  vehicle_count: number;
  halted_count: number;
  type_composition: TypeComposition;
  wait_time_current: number;
  wait_time_max_single_vehicle: number;
  starvation_flag: boolean;
}

export interface VisionLane extends Lane {
  confidence: number;
  source: string;
}

export interface Junction {
  lanes: Record<string, Lane>;
  vision: Record<string, VisionLane>;
  current_phase: number;
  lane_count: number;
}

export interface V2XMessage {
  vehicle_id: string;
  position: { x: number; y: number };
  speed: number;
  heading: number;
  timestamp: number;
  delay_ms: number;
  dropped: boolean;
}

export interface ActiveIncident {
  incident_id: string;
  type: string;
  location: { junction_id: JunctionId; lane_id: string };
  severity: string;
  affected_lanes: string[];
  reported_at_sim_time: number;
  estimated_duration_s: number;
}

export interface DigitalTwin {
  sim_time: number;
  corridor_adjacency: [JunctionId, JunctionId][];
  junctions: Record<JunctionId, Junction>;
  active_incidents: ActiveIncident[];
  weather: { state: string; changed_at_sim_time: number };
  v2x_messages_recent: V2XMessage[];
}

/** The six values §12.2 can produce. `raw_count` and `emergency_override` are
 *  the only two present in the fixture; the rest arrive live. */
export type DecisionReason =
  | 'raw_count'
  | 'emergency_override'
  | 'starvation_ceiling'
  | 'starvation_bonus'
  | 'rl_policy'
  | 'voice_command';

export interface Decision {
  sim_time: number;
  junction_id: JunctionId;
  phase_selected: number;
  score_breakdown: Record<string, number>;
  alternative_scores: Record<string, number>;
  reason: DecisionReason | string;
  lane_id: string;
  direction: Approach | string;
  lane_slot: number;
}

export interface MetricsSnapshot {
  wait_time_variance_across_lanes: number;
  mean_wait_max: number;
  starvation_events_total: number;
  throughput_total: number;
}

export interface AgentActivity {
  agent: string;
  role: string;
  wraps: string;
  kind: string;
  said: string;
  at: number;
  step: number;
  detail: Record<string, unknown>;
}

/** Additive — present only on some frames. Never assume. */
export interface IncidentAlert {
  type: string;
  junction: JunctionId;
  approach: Approach;
  lane_index: number;
  /** null until vision calibration lands — render "range calibrating", not 0 m. */
  distance_m: number | null;
  distance_confidence: number | null;
  severity: string;
  detected_at: number;
  source: string;
}

export interface SpilloverPrediction {
  from_junction: JunctionId;
  to_junction: JunctionId;
  horizon_s: number;
  predicted_queue_delta: number;
  confidence: number;
}

export interface ResponderMessage {
  event: string;
  junction_id: JunctionId;
  lane_id: string;
  sim_time: number;
  clearance_time_s: number;
  baseline_clearance_time_s: number;
  baseline_is_estimate: boolean;
  baseline_is_worst_case: boolean;
  improvement_pct: number;
  override_fired: boolean;
  trigger_source: 'detected' | 'operator' | string;
  served_on_arrival?: boolean;
  summary: string;
}

export interface Frame {
  sim_time: number;
  digital_twin: DigitalTwin;
  decision: Decision;
  narration: string;
  metrics_snapshot: MetricsSnapshot;
  agent_activity: AgentActivity[];
  predictions?: { spillover: SpilloverPrediction[] };
  incident_alerts?: IncidentAlert[];
  responder_messages?: ResponderMessage[];
  /** Live-only. Absent from the fixture entirely — panels degrade quietly. */
  iot_sensors?: unknown;
  shadow_advisor?: unknown;
}

export const JUNCTION_IDS: JunctionId[] = ['J1', 'J2', 'J3'];
