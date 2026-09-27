// Shared, pure workflow rules. Counts and stages are derived from stored facts.
export const PHASES = [
  { id: 'research', label: 'Research' }, { id: 'design', label: 'Design & content' },
  { id: 'build', label: 'Build & QA' }, { id: 'launch', label: 'Launch review' },
  { id: 'grow', label: 'Growing' },
];
export function deliveryPhase(card) {
  const s = card.curStage?.id || '';
  if (card.p.status === 'live') return 'grow';
  if (card.finished) return card.p.playbook === 'seo_campaign' ? 'grow' : 'launch';
  if (card.p.playbook === 'seo_campaign') return 'grow';
  if (/review_gate|launch/.test(s)) return 'launch';
  if (/build|audit|repair|visual|render_check/.test(s)) return 'build';
  if (/copy|schema|seo_plan|aeo|design/.test(s)) return 'design';
  return 'research';
}
export function isTestProject(p) {
  return /(?:^zz[\s_-]|\btest\b|\bselftest\b|\be2e\b)/i.test(p.name || '');
}
export function safeWebsiteUrl(value) {
  try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; }
  catch { return ''; }
}
export function recoveryMessage(error) {
  const s = String(error || '');
  if (/credits|billing|\b402\b/i.test(s)) return {title: 'Provider credits exhausted', detail: 'Restore credits with your AI provider, then resume. Completed steps are saved.', action: 'Check connections'};
  if (/401|403|unauthori[sz]ed|api.key|authentication/i.test(s)) return {title: 'Connection needs attention', detail: 'Check the provider connection, then resume from the saved step.', action: 'Check connections'};
  if (/timeout|timed out|429|rate.limit/i.test(s)) return {title: 'Provider temporarily unavailable', detail: 'Wait for the provider to recover, then resume. Completed steps are saved.', action: 'Review & resume'};
  return {title: 'Build needs attention', detail: 'Review the failed step and its output before resuming. Completed steps are saved.', action: 'Review & resume'};
}
export function relativeChange(current, previous) {
  if (!Number.isFinite(current) || !Number.isFinite(previous) || previous === 0) return null;
  return Math.round((current - previous) / previous * 100);
}
