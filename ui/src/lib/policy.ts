// Policy-as-code rule table (docs/DEMO_DECK_PLAN.md 1.5 and 3.5). Shown as tooltips on rule ids and on the Policy tab.
export interface RuleDoc { id: string; stage: 'write' | 'gate'; title: string; text: string; code: string }

export const RULES: RuleDoc[] = [
  {
    id: 'write.lower_tier_conflict', stage: 'write', title: 'Lower trust cannot overwrite higher trust',
    text: 'A claim that contradicts a record of a higher tier is held in quarantine until a T3 source or a reviewer confirms it.',
    code: 'if claim.tier < existing.tier and nli(claim, existing) == "contradiction":\n    verdict = QUARANTINE',
  },
  {
    id: 'write.no_conflict', stage: 'write', title: 'Nothing to contradict',
    text: 'No higher-tier fact covers the claim. It is committed with its source tier; T0 standing instructions are stored as quoted data only.',
    code: 'if no_conflict(claim):\n    verdict = COMMIT  # tier stays = channel tier',
  },
  {
    id: 'write.derived_low_water_mark', stage: 'write', title: 'Derived records take the lowest parent tier',
    text: 'A summary or note can never be more trusted than its least trusted input (Biba low-water mark).',
    code: 'record.tier = min(parent.tier for parent in record.parents)',
  },
  {
    id: 'write.sor_sync', stage: 'write', title: 'System-of-record sync',
    text: 'Written by the ERP / CRM connector, which holds the store credentials: committed as T3.',
    code: 'tier = TIER_MAP[channel]  # erp -> T3',
  },
  {
    id: 'write.user_statement', stage: 'write', title: 'Authenticated user statement',
    text: 'A statement from the signed-in user about their own work: committed as T2.',
    code: 'tier = TIER_MAP["user"]  # T2',
  },
  {
    id: 'write.reconfirm', stage: 'write', title: 'Re-sync, nothing changed',
    text: 'Identical to an existing record: reconfirmed, no new version.',
    code: 'if claim == existing: reconfirm(existing)',
  },
  {
    id: 'write.rederive_clean', stage: 'write', title: 'Re-derive after rollback',
    text: 'A reverted summary is rebuilt from its clean parents only.',
    code: 'rederive(old, parents=[p for p in old.parents if p not in closure])',
  },
  {
    id: 'quarantine.confirm_t3', stage: 'write', title: 'Confirmed via T3',
    text: 'The vendor was updated in the ERP (call-back first); the claim is promoted and the old fact is closed in time, not deleted.',
    code: 'promote(claim, tier="T3"); close(old, valid_to=now)',
  },
  {
    id: 'gate.payment.payee_requires_T3', stage: 'gate', title: 'Payment payee / account / amount need T3',
    text: 'pay_invoice.payee, .account and .amount are critical arguments and need T3 provenance. Lowest tier T2: ask the user. T0 or T1: block and show "verify by call-back, then update the vendor in ERP".',
    code: 'for arg in (payee, account, amount):\n    t = min(tier(m) for m in sources(arg))\n    if t >= T3: ok\n    elif t == T2: ASK\n    else: BLOCK',
  },
  {
    id: 'gate.payment.critical_args_T3', stage: 'gate', title: 'All critical arguments are T3',
    text: 'Every critical argument is traced to system-of-record memories: the call is allowed.',
    code: 'if all(min_tier(arg) >= T3 for arg in critical): ALLOW',
  },
];

export const ruleDoc = (id: string): RuleDoc | undefined => RULES.find((r) => r.id === id);
