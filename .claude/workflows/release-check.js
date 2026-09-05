// Release check: deterministic verification, then independent review lenses over the diff.
// Workflow({scriptPath: '.claude/workflows/release-check.js', args: {base: 'main', lenses: ['code','security','data']}})
// Agents used (see .claude/agents/): forge-verifier, forge-reviewer-code, forge-reviewer-security,
// forge-reviewer-data, and forge-reviewer-ui when the diff touches templates, static files or bot texts.
export const meta = {
  name: 'release-check',
  description: 'Verify make check, then review the diff with code/security/data lenses before a deploy',
  phases: [
    { title: 'Verify', detail: 'make check with denominators' },
    { title: 'Review', detail: 'independent lenses over the diff' },
  ],
}

const BASE = (args && args.base) || 'main'
const LENSES = (args && args.lenses && args.lenses.length) ? args.lenses : ['code', 'security', 'data']

const contract = `Repository: the current working directory of this session (the repo root; run every shell command there). Branch under review: HEAD, base: ${BASE}.
Rules (CLAUDE.md): never delete participant data or media; migrations additive and reversible; tests/acceptance are read-only.
Deterministic checks: make check (ruff, ruff format --check, mypy romantika, pytest).`

const VERIFY_SCHEMA = {
  type: 'object', required: ['green', 'report'],
  properties: { green: { type: 'boolean' }, report: { type: 'string' } },
}
const REVIEW_SCHEMA = {
  type: 'object', required: ['findings', 'checked'],
  properties: {
    findings: { type: 'array', items: { type: 'object',
      required: ['severity', 'where', 'quote', 'scenario'],
      properties: {
        severity: { type: 'string', enum: ['critical', 'important', 'nit'] },
        where: { type: 'string' }, quote: { type: 'string' }, scenario: { type: 'string' },
      } } },
    checked: { type: 'array', items: { type: 'string' } },
  },
}

phase('Verify')
const verify = await agent(`Run the deterministic checks and report honestly with denominators (tests passed / total, exit codes).\n${contract}`,
  { label: 'verify', phase: 'Verify', agentType: 'forge-verifier', schema: VERIFY_SCHEMA })

phase('Review')
const reviews = (await parallel(LENSES.map(l => () =>
  agent(`Review the diff \`git diff ${BASE}..HEAD\` through your lens. Every finding needs file:line, a code quote and a failure scenario; nits separately.\n${contract}`,
    { label: `review-${l}`, phase: 'Review', agentType: `forge-reviewer-${l}`, schema: REVIEW_SCHEMA })
))).filter(Boolean)

const findings = reviews.flatMap(r => r.findings)
const blocking = findings.filter(f => f.severity !== 'nit')
return {
  green: verify.green && blocking.length === 0,
  verify: verify.report,
  blocking,
  nits: findings.filter(f => f.severity === 'nit'),
  checked: reviews.flatMap(r => r.checked),
}
