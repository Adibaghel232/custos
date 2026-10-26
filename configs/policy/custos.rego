# Write-path decision table, SPEC §5.5. Mirrors custos/policy/engine.py::decide (parity-tested).
package custos.write

import rego.v1

default decision := {"action": "commit", "rule": "R8_DEFAULT"}

decision := {"action": "quarantine", "rule": "R0_CHECKER_UNAVAILABLE"} if {
	input.checks_unavailable
} else := {"action": "reject", "rule": "R1_UNGROUNDED"} if {
	input.all_claims_ungrounded
} else := {"action": "quarantine", "rule": "R2_LOW_TIER_INSTRUCTION"} if {
	input.has_instruction
	input.tier < 2
} else := {"action": "quarantine", "rule": "R3_INSTRUCTION_NEEDS_CONFIRM"} if {
	input.has_instruction
	not input.confirmed
} else := {"action": "quarantine", "rule": "R4_DRIFT_ALARM"} if {
	input.drift_alarm
	input.tier <= 1
} else := {"action": "version", "rule": "R5b_CORROBORATED"} if {
	input.has_conflict
	input.max_conflict_tier > input.tier
	input.corroboration_ok
} else := {"action": "quarantine", "rule": "R5_LOWER_TIER_CONFLICT"} if {
	input.has_conflict
	input.max_conflict_tier > input.tier
} else := {"action": "quarantine", "rule": "R7_CRITICAL_UNVERIFIED"} if {
	input.critical_attribute
	input.tier <= 1
	not input.sor_verified
} else := {"action": "version", "rule": "R6_SUPERSEDE"} if {
	input.has_conflict
}
