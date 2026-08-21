import fs from 'node:fs'

const source = fs.readFileSync(new URL('../lib/test-counts.js', import.meta.url), 'utf8')
const mod = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)

const cases = Array.from({ length: 100 }, (_, i) => ({ title: `case ${i + 1}`, status: 'passed' }))
const report = {
  numPassedTests: 90,
  numTotalTests: 90,
  testResults: [{ name: 'tests/example.test.js', assertionResults: cases }],
}
const counts = mod.deriveVitestCounts(report)
if (counts.passed !== 100 || counts.total !== 100 || counts.failed !== 0) {
  throw new Error(`expected 100/100 from assertion rows, got ${counts.passed}/${counts.total}`)
}
console.log('Vitest count regression: 100/100 OK (stale summary ignored)')
