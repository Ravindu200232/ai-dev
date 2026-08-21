export function deriveVitestCounts(v) {
  const suites = Array.isArray(v?.testResults) ? v.testResults : []
  const cases = suites.flatMap(s => Array.isArray(s?.assertionResults) ? s.assertionResults : [])
  const passed = cases.filter(c => c?.status === 'passed').length
  const failed = cases.filter(c => c?.status === 'failed').length
  const skipped = cases.filter(c => ['skipped', 'pending', 'todo'].includes(c?.status)).length
  const total = cases.length
  const executed = passed + failed
  return { passed, failed, skipped, total, executed, files: suites.length }
}

export function suiteRows(v) {
  return (v?.testResults || []).map(t => ({
    file: String(t?.name || t?.testFilePath || '').replace(/\\/g, '/').split('/tests/').pop(),
    cases: t?.assertionResults || [],
    status: t?.status || '',
  }))
}
