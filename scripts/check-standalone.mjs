// Standalone guard (T01, aturan #1).
//
// marshal-threejs must never integrate with marshal-ai (Python) or marshal-fe (React):
// no cross-folder imports, no shared packages, no HTTP calls to those projects.
// Documents (tasks/, README.md) may name the projects; source code may not.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const root = process.cwd()
const scanDirs = ['src', 'scripts']
const scanExts = new Set(['.ts', '.tsx', '.js', '.mjs', '.jsx'])
// This guard names the projects on purpose; skip it.
const selfPath = normalize('scripts/check-standalone.mjs')

function normalize(p) {
  return p.split('\\').join('/')
}

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    const stat = statSync(full)
    if (stat.isDirectory()) {
      if (entry === 'node_modules' || entry === '.git') continue
      walk(full, out)
    } else {
      out.push(full)
    }
  }
  return out
}

function extOf(p) {
  const i = p.lastIndexOf('.')
  return i === -1 ? '' : p.slice(i)
}

const violations = []

// Patterns that indicate integration into a sibling project.
const checks = [
  {
    re: /\bmarshal-(ai|fe)\b/,
    msg: 'references marshal-ai/marshal-fe',
  },
  {
    re: /(?:from|import|require)\s*\(?\s*['"][^'"]*\.\.\/[^'"]*marshal-(ai|fe)/,
    msg: 'relative import escapes the project into marshal-ai/marshal-fe',
  },
  {
    re: /https?:\/\/[^'"\s]*marshal-(ai|fe)/,
    msg: 'HTTP call to marshal-ai/marshal-fe host',
  },
  {
    re: /localhost:(8000|8001|8100)\b/,
    msg: 'reference to a sibling project dev port',
  },
]

for (const dir of scanDirs) {
  let files
  try {
    files = walk(join(root, dir))
  } catch {
    continue
  }
  for (const file of files) {
    if (!scanExts.has(extOf(file))) continue
    const rel = normalize(relative(root, file))
    if (rel === selfPath) continue
    const text = readFileSync(file, 'utf8')
    const lines = text.split('\n')
    for (const { re, msg } of checks) {
      lines.forEach((line, idx) => {
        if (re.test(line)) {
          violations.push(`${rel}:${idx + 1} — ${msg}`)
        }
      })
    }
  }
}

if (violations.length > 0) {
  console.error('✗ standalone check failed:')
  for (const v of violations) console.error(`  ${v}`)
  process.exit(1)
}

console.log('✓ standalone check passed (no marshal-ai/marshal-fe integration)')
