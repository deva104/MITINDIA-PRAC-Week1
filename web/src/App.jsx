import { useEffect, useRef, useState } from 'react'

// Dev requests go through the Vite proxy on this origin (see vite.config.js) because the FastAPI
// service sends no CORS headers. Point VITE_API_BASE at http://localhost:8000 to skip the proxy.
const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

const NAMASTE_SYSTEM = 'https://namaste.ayush.gov.in/fhir/CodeSystem/namaste'
const MMS_SYSTEM = 'http://id.who.int/icd/release/11/mms'
const SOURCE_EXTENSION = 'https://namaste.ayush.gov.in/fhir/StructureDefinition/expansion-source'

// The backend tags every $translate match with where it came from.
const TM2_SEED_SOURCE = 'namaste-tm2-seed'
const WHO_SEARCH_SOURCE = 'who-search'

// ICD-11 chapter 26 (traditional medicine) codes.
const TM2_CODE_PATTERN = /^S[K-T]/

const PATIENT = { name: 'Radha Rao', gender: 'female', birthDate: '1963-05-12' }
const ABHA = '91-1111-1111-1111'
const RECORDER = 'Practitioner/dr-iyer'

const SEARCH_DEBOUNCE_MS = 250
const SEARCH_COUNT = 12
const AUDIT_POLL_MS = 4000

const BADGES = {
  namaste: { label: 'NAMASTE', className: 'bg-teal-50 text-teal-700 ring-teal-600/20' },
  tm2: { label: 'ICD-11 TM2', className: 'bg-amber-50 text-amber-700 ring-amber-600/20' },
  biomed: { label: 'ICD-11 Bio', className: 'bg-blue-50 text-blue-700 ring-blue-600/20' },
}

// $expand ships the traditional term as designations. The diacritical form is the properly
// rendered one ("sandhigatavātaḥ"); the roman form is an ASCII transliteration where capitals
// encode long vowels ("sandhigatavAtaH"), so it is shown verbatim and never re-cased.
const TRADITIONAL_USES = ['diacritical', 'roman']

/** The traditional term for a NAMASTE expansion entry, or null when none was published. */
function traditionalTerm(entry) {
  for (const use of TRADITIONAL_USES) {
    const match = (entry?.designation ?? []).find((item) => item.use?.code === use && item.value)
    if (match) return match.value
  }
  return null
}

/** Which code system an expansion entry or a stored coding belongs to. */
function sourceOf(entry) {
  const tagged = entry.extension?.find((item) => item.url === SOURCE_EXTENSION)?.valueCode
  if (tagged) return tagged
  if (entry.system === NAMASTE_SYSTEM) return 'namaste'
  return TM2_CODE_PATTERN.test(entry.code ?? '') ? 'tm2' : 'biomed'
}

/** Error text the API put in an OperationOutcome, if any. */
function diagnosticsOf(body) {
  const diagnostics = (body?.issue ?? []).map((issue) => issue.diagnostics).filter(Boolean)
  return diagnostics.length ? diagnostics.join('; ') : null
}

async function fetchJson(path, options) {
  const response = await fetch(`${API_BASE}${path}`, options)
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    // Consent refusals are plain {reason}; auth failures are OperationOutcome diagnostics.
    throw new Error(body?.reason ?? diagnosticsOf(body) ?? `${response.status} ${response.statusText}`)
  }
  return body
}

/** Flatten a $translate Parameters resource into plain match objects. */
function parseMatches(parameters) {
  return (parameters?.parameter ?? [])
    .filter((parameter) => parameter.name === 'match')
    .map((match) => {
      const part = (name) => (match.part ?? []).find((item) => item.name === name)
      const coding = part('concept')?.valueCoding ?? {}
      return {
        code: coding.code,
        display: coding.display,
        equivalence: part('equivalence')?.valueCode,
        source: part('source')?.valueString,
        confidence: part('confidence')?.valueDecimal ?? null,
      }
    })
}

function Badge({ source }) {
  const badge = BADGES[source] ?? BADGES.biomed
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${badge.className}`}
    >
      {badge.label}
    </span>
  )
}

function Panel({ title, subtitle, children, actions }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold tracking-wide text-slate-900 uppercase">{title}</h2>
          {subtitle && <p className="mt-1 text-xs text-slate-500">{subtitle}</p>}
        </div>
        {actions}
      </header>
      <div className="px-5 py-4">{children}</div>
    </section>
  )
}

function CodingRow({ coding }) {
  return (
    <div className="flex items-baseline gap-3 py-1">
      <Badge source={sourceOf(coding)} />
      <code className="font-mono text-sm font-semibold text-slate-800">{coding.code}</code>
      <span className="text-sm text-slate-600">{coding.display ?? '(no display)'}</span>
    </div>
  )
}

function Notice({ tone = 'info', children }) {
  const tones = {
    info: 'bg-slate-50 text-slate-600 border-slate-200',
    error: 'bg-rose-50 text-rose-700 border-rose-200',
    warn: 'bg-amber-50 text-amber-800 border-amber-200',
  }
  return <p className={`rounded-lg border px-3 py-2 text-sm ${tones[tone]}`}>{children}</p>
}

export default function App() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState(null)

  const [selected, setSelected] = useState(null) // { code, display } from a NAMASTE row
  const [matches, setMatches] = useState(null)
  const [translating, setTranslating] = useState(false)
  const [translateError, setTranslateError] = useState(null)

  // The clinician's biomedicine choice: a code string, or null for "leave pending".
  const [biomedChoice, setBiomedChoice] = useState(null)
  // A biomedicine code picked straight from the search dropdown, kept alongside the suggestions.
  const [searchPick, setSearchPick] = useState(null)

  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [saved, setSaved] = useState(null)
  const [showRaw, setShowRaw] = useState(false)

  const [problemList, setProblemList] = useState(null)
  const [problemError, setProblemError] = useState(null)

  // Mock ABHA session for the auth-gated POST /diagnosis; search/translate stay open without it.
  const [token, setToken] = useState(null)
  const [consent, setConsent] = useState('granted')
  const [auditOk, setAuditOk] = useState(null)

  const searchRun = useRef(0)

  useEffect(() => {
    fetchJson('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ abha: ABHA }),
    })
      .then((body) => setToken(body.access_token))
      .catch(() => setToken(null))
  }, [])

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const body = await fetchJson('/audit/verify')
        if (!cancelled) setAuditOk(body.ok === true)
      } catch {
        if (!cancelled) setAuditOk(null)
      }
    }
    poll()
    const timer = setInterval(poll, AUDIT_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  async function toggleConsent() {
    const next = consent === 'granted' ? 'revoked' : 'granted'
    try {
      const body = await fetchJson('/consent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ abha: ABHA, status: next }),
      })
      setConsent(body.status)
    } catch (error) {
      setSaveError(`Consent update failed: ${error.message}`)
    }
  }

  // Debounced autocomplete against $expand.
  useEffect(() => {
    const cleaned = query.trim()
    if (!cleaned) {
      setResults(null)
      setSearchError(null)
      setSearching(false)
      return
    }

    const run = ++searchRun.current
    setSearching(true)
    const timer = setTimeout(async () => {
      try {
        const path = `/ValueSet/$expand?filter=${encodeURIComponent(cleaned)}&count=${SEARCH_COUNT}`
        const valueSet = await fetchJson(path)
        if (run !== searchRun.current) return // a newer keystroke already won
        setResults(valueSet?.expansion?.contains ?? [])
        setSearchError(null)
      } catch (error) {
        if (run !== searchRun.current) return
        setResults(null)
        setSearchError(error.message)
      } finally {
        if (run === searchRun.current) setSearching(false)
      }
    }, SEARCH_DEBOUNCE_MS)

    return () => clearTimeout(timer)
  }, [query])

  async function selectNamaste(entry) {
    setSelected(entry) // kept whole so the card can read its designations
    setMatches(null)
    setTranslateError(null)
    setBiomedChoice(null)
    setSaved(null)
    setSaveError(null)
    setProblemList(null)
    setTranslating(true)
    try {
      const parameters = await fetchJson(`/ConceptMap/$translate?code=${encodeURIComponent(entry.code)}`)
      setMatches(parseMatches(parameters))
    } catch (error) {
      setTranslateError(error.message)
    } finally {
      setTranslating(false)
    }
  }

  const curated = (matches ?? []).filter((match) => match.source === TM2_SEED_SOURCE)
  const suggested = (matches ?? []).filter((match) => match.source === WHO_SEARCH_SOURCE)
  const tm2 = curated[0] ?? null

  // Suggestions from $translate, plus anything the clinician picked out of the search dropdown.
  const biomedOptions = [...suggested]
  if (searchPick && !biomedOptions.some((option) => option.code === searchPick.code)) {
    biomedOptions.push({ ...searchPick, source: 'search-pick', confidence: null, equivalence: 'inexact' })
  }
  const chosenBiomed = biomedOptions.find((option) => option.code === biomedChoice) ?? null

  async function recordDiagnosis() {
    if (!selected || !tm2) return
    setSaving(true)
    setSaveError(null)
    setShowRaw(false)
    setProblemError(null)
    try {
      const body = {
        patient: PATIENT,
        namaste_code: selected.code,
        tm2_code: tm2.code,
        biomed_code: chosenBiomed?.code ?? null,
        recorder: RECORDER,
      }
      const result = await fetchJson('/diagnosis', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
      })
      setSaved(result)

      if (result?.patient_id) {
        try {
          const list = await fetchJson(`/Patient/${encodeURIComponent(result.patient_id)}/problem-list`)
          setProblemList(list?.conditions ?? [])
        } catch (error) {
          setProblemError(error.message)
        }
      }
    } catch (error) {
      setSaveError(error.message)
    } finally {
      setSaving(false)
    }
  }

  const storedCodings = saved?.condition?.code?.coding ?? []

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-6 py-4">
          <div>
            <h1 className="text-lg font-semibold">AYUSH EMR — NAMASTE ⇄ ICD-11 dual coding</h1>
            <p className="mt-0.5 text-xs text-slate-500">
              Terminology served by the local FHIR R4 service; ICD-11 titles come live from the WHO container.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-2 rounded-full bg-teal-50 px-3 py-1.5 text-sm font-medium text-teal-800 ring-1 ring-teal-600/20 ring-inset">
              <span className="h-2 w-2 rounded-full bg-teal-500" />
              Patient: {PATIENT.name} (ABHA {ABHA})
            </span>
          </div>
        </div>
        <div className="mx-auto max-w-5xl px-6 pb-3">
          <p className="text-xs text-amber-800">
            Demo: the ABHA number, the consent step and the practitioner identity are simulated. No real ABDM
            authentication or consent artefact is involved.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="font-semibold tracking-wide text-slate-500 uppercase">Compliance</span>
            <span
              className={`inline-flex items-center rounded-full px-2.5 py-1 font-medium ring-1 ring-inset ${
                token
                  ? 'bg-emerald-50 text-emerald-800 ring-emerald-600/20'
                  : 'bg-slate-100 text-slate-500 ring-slate-300'
              }`}
            >
              ABHA session: {token ? 'mock' : '…'}
            </span>
            <button
              type="button"
              onClick={toggleConsent}
              className={`inline-flex items-center rounded-full px-2.5 py-1 font-medium ring-1 ring-inset ${
                consent === 'granted'
                  ? 'bg-emerald-50 text-emerald-800 ring-emerald-600/20'
                  : 'bg-rose-50 text-rose-800 ring-rose-600/20'
              }`}
              title="POST /consent — simulated in-memory gate"
            >
              Consent: {consent}
            </button>
            <span
              className={`inline-flex items-center rounded-full px-2.5 py-1 font-medium ring-1 ring-inset ${
                auditOk === true
                  ? 'bg-emerald-50 text-emerald-800 ring-emerald-600/20'
                  : auditOk === false
                    ? 'bg-rose-50 text-rose-800 ring-rose-600/20'
                    : 'bg-slate-100 text-slate-500 ring-slate-300'
              }`}
            >
              Audit chain:{' '}
              {auditOk === true ? 'intact ✓' : auditOk === false ? 'BROKEN ✗' : '…'}
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-5xl gap-5 px-6 py-6">
        <Panel
          title="1 · Find a diagnosis"
          subtitle="Type an Ayurvedic term or an English condition. NAMASTE comes from Postgres, ICD-11 rows live from WHO."
        >
          <div className="relative">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="e.g. sandhigata, osteoarthritis of knee, jvara"
              className="w-full rounded-lg border border-slate-300 px-4 py-2.5 text-sm outline-none placeholder:text-slate-400 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20"
            />
            {searching && (
              <span className="absolute top-3 right-3 text-xs text-slate-400">searching…</span>
            )}
          </div>

          {searchError && (
            <div className="mt-3">
              <Notice tone="error">
                Search failed: {searchError}. Is the API up on <code>http://localhost:8000</code>?
              </Notice>
            </div>
          )}

          {results && results.length === 0 && !searching && (
            <div className="mt-3">
              <Notice>No matches for “{query.trim()}”. Try a shorter stem, e.g. “sandhi”.</Notice>
            </div>
          )}

          {results && results.length > 0 && (
            <ul className="mt-3 max-h-80 divide-y divide-slate-100 overflow-y-auto rounded-lg border border-slate-200">
              {results.map((entry, index) => {
                const source = sourceOf(entry)
                const isNamaste = source === 'namaste'
                const isBiomed = source === 'biomed'
                const clickable = isNamaste || isBiomed
                const traditional = isNamaste ? traditionalTerm(entry) : null
                return (
                  <li key={`${entry.system}-${entry.code}-${index}`}>
                    <button
                      type="button"
                      disabled={!clickable}
                      onClick={() => {
                        if (isNamaste) selectNamaste(entry)
                        else if (isBiomed) {
                          setSearchPick({ code: entry.code, display: entry.display })
                          setBiomedChoice(entry.code)
                        }
                      }}
                      className={`flex w-full items-baseline gap-3 px-4 py-2.5 text-left ${
                        clickable ? 'hover:bg-slate-50' : 'cursor-default'
                      } ${selected?.code === entry.code && isNamaste ? 'bg-teal-50/60' : ''}`}
                      title={
                        isNamaste
                          ? 'Select this NAMASTE code'
                          : isBiomed
                            ? 'Attach this biomedicine code to the diagnosis'
                            : 'TM2 is linked automatically from the NAMASTE mapping'
                      }
                    >
                      <Badge source={source} />
                      <code className="font-mono text-sm font-semibold text-slate-800">{entry.code}</code>
                      {traditional ? (
                        <span className="min-w-0">
                          <span className="block text-sm font-medium text-slate-800">{traditional}</span>
                          {entry.display && <span className="block text-xs text-slate-500">{entry.display}</span>}
                        </span>
                      ) : (
                        <span className="text-sm text-slate-600">{entry.display ?? '(no display)'}</span>
                      )}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </Panel>

        {selected && (
          <Panel
            title="2 · Dual coding"
            subtitle="TM2 is linked automatically from the NAMASTE mapping. Biomedicine is a ranked suggestion that the clinician confirms."
          >
            <div className="grid gap-3">
              <article className="rounded-lg border border-teal-200 bg-teal-50/40 p-4">
                <div className="flex items-center gap-2">
                  <Badge source="namaste" />
                  <span className="text-xs text-slate-500">selected by clinician</span>
                </div>
                <p className="mt-2 font-mono text-sm font-semibold">{selected.code}</p>
                <p className="text-sm font-medium text-slate-800">
                  {traditionalTerm(selected) ?? selected.display ?? '(no display)'}
                </p>
                {traditionalTerm(selected) && selected.display && (
                  <p className="text-xs text-slate-500">{selected.display}</p>
                )}
              </article>

              <article className="rounded-lg border border-amber-200 bg-amber-50/40 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge source="tm2" />
                  <span className="text-xs text-slate-500">auto-linked from the NAMASTE export</span>
                </div>
                {translating && <p className="mt-2 text-sm text-slate-500">resolving mapping…</p>}
                {translateError && (
                  <div className="mt-2">
                    <Notice tone="error">Translate failed: {translateError}</Notice>
                  </div>
                )}
                {!translating && !translateError && !tm2 && (
                  <div className="mt-2">
                    <Notice tone="warn">
                      No curated TM2 mapping exists for {selected.code}. The service will not invent one, and{' '}
                      <code>/diagnosis</code> needs a TM2 code, so this concept cannot be recorded yet.
                    </Notice>
                  </div>
                )}
                {tm2 && (
                  <>
                    <p className="mt-2 font-mono text-sm font-semibold">{tm2.code}</p>
                    <p className="text-sm text-slate-700">{tm2.display ?? '(no display)'}</p>
                    <span className="mt-2 inline-flex items-center rounded-md bg-white px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-amber-600/20 ring-inset">
                      equivalence: {tm2.equivalence ?? 'relatedto'}
                    </span>
                    {curated.length > 1 && (
                      <p className="mt-2 text-xs text-slate-500">
                        {curated.length - 1} further curated TM2 target(s):{' '}
                        {curated
                          .slice(1)
                          .map((match) => match.code)
                          .join(', ')}
                      </p>
                    )}
                  </>
                )}
              </article>

              <article className="rounded-lg border border-blue-200 bg-blue-50/40 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge source="biomed" />
                  <span className="text-xs text-slate-500">
                    live WHO candidates — many-to-many, clinician confirms one
                  </span>
                </div>

                {translating && <p className="mt-2 text-sm text-slate-500">asking WHO for candidates…</p>}

                {!translating && biomedOptions.length === 0 && (
                  <div className="mt-2">
                    <Notice>
                      No biomedicine candidate returned. Search an English term above and click an{' '}
                      <span className="font-semibold">ICD-11 Bio</span> row to attach one, or record the diagnosis with
                      biomedicine pending.
                    </Notice>
                  </div>
                )}

                {biomedOptions.length > 0 && (
                  <ul className="mt-3 grid gap-2">
                    {biomedOptions.map((option) => (
                      <li key={option.code}>
                        <label
                          className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${
                            biomedChoice === option.code
                              ? 'border-blue-400 bg-white'
                              : 'border-slate-200 bg-white/60 hover:bg-white'
                          }`}
                        >
                          <input
                            type="radio"
                            name="biomed"
                            className="mt-1 accent-blue-600"
                            checked={biomedChoice === option.code}
                            onChange={() => setBiomedChoice(option.code)}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-baseline gap-2">
                              <code className="font-mono text-sm font-semibold">{option.code}</code>
                              <span className="text-sm text-slate-700">{option.display ?? '(no display)'}</span>
                            </span>
                            {option.confidence != null ? (
                              <span className="mt-2 flex items-center gap-2">
                                <span className="h-1.5 w-40 overflow-hidden rounded-full bg-slate-200">
                                  <span
                                    className="block h-full rounded-full bg-blue-500"
                                    style={{ width: `${Math.round(option.confidence * 100)}%` }}
                                  />
                                </span>
                                <span className="text-xs text-slate-500">
                                  confidence {(option.confidence * 100).toFixed(0)}% · WHO search
                                </span>
                              </span>
                            ) : (
                              <span className="mt-1 block text-xs text-slate-500">picked from ICD-11 search</span>
                            )}
                          </span>
                        </label>
                      </li>
                    ))}
                    <li>
                      <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-200 bg-white/60 p-3 hover:bg-white">
                        <input
                          type="radio"
                          name="biomed"
                          className="accent-slate-500"
                          checked={biomedChoice === null}
                          onChange={() => setBiomedChoice(null)}
                        />
                        <span className="text-sm text-slate-600">
                          None of these — record NAMASTE + TM2 and mark biomedicine pending
                        </span>
                      </label>
                    </li>
                  </ul>
                )}
              </article>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={recordDiagnosis}
                disabled={saving || !tm2 || !token}
                className="rounded-lg bg-teal-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-teal-800 disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {saving ? 'Recording…' : 'Record diagnosis'}
              </button>
              <span className="text-xs text-slate-500">
                Writes one FHIR Condition (Patient + Provenance in the same transaction) to HAPI.
              </span>
            </div>

            {saveError && (
              <div className="mt-3">
                <Notice tone="error">Could not record the diagnosis: {saveError}</Notice>
              </div>
            )}
          </Panel>
        )}

        {saved && (
          <Panel
            title="3 · Saved to Problem List"
            subtitle="Stored in FHIR (HAPI) as one CodeableConcept with 3 codings."
            actions={
              <button
                type="button"
                onClick={() => setShowRaw((value) => !value)}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                {showRaw ? 'Hide raw FHIR' : 'View raw FHIR'}
              </button>
            }
          >
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="rounded-md bg-slate-100 px-2 py-1 font-mono text-xs">
                Condition/{saved.condition_id ?? '?'}
              </span>
              <span className="rounded-md bg-slate-100 px-2 py-1 font-mono text-xs">
                Patient/{saved.patient_id ?? '?'}
              </span>
              <span className="text-xs text-slate-500">transaction: {saved.hapi_status ?? 'unknown'}</span>
            </div>

            {saved.condition?.code?.text && (
              <p className="mt-3 text-sm font-medium text-slate-800">{saved.condition.code.text}</p>
            )}

            <div className="mt-2 divide-y divide-slate-100">
              {storedCodings.length === 0 ? (
                <Notice tone="warn">
                  The Condition was created but could not be read back, so its codings are unavailable.
                </Notice>
              ) : (
                storedCodings.map((coding) => <CodingRow key={`${coding.system}-${coding.code}`} coding={coding} />)
              )}
            </div>

            {storedCodings.length < 3 && storedCodings.length > 0 && (
              <div className="mt-3">
                <Notice tone="warn">
                  {storedCodings.length} coding(s) stored — biomedicine was left pending, which the service records
                  rather than guessing a code.
                </Notice>
              </div>
            )}

            {saved.issues && (
              <div className="mt-3">
                <Notice tone="warn">HAPI reported per-entry issues: {JSON.stringify(saved.issues)}</Notice>
              </div>
            )}

            {showRaw && (
              <pre className="mt-4 max-h-96 overflow-auto rounded-lg bg-slate-900 p-4 text-xs leading-relaxed text-slate-100">
                {JSON.stringify(saved.condition, null, 2)}
              </pre>
            )}
          </Panel>
        )}

        {saved && (
          <Panel title="4 · Problem list" subtitle={`GET /Patient/${saved.patient_id ?? '?'}/problem-list`}>
            {problemError && <Notice tone="error">Could not load the problem list: {problemError}</Notice>}
            {!problemError && problemList === null && <p className="text-sm text-slate-500">loading…</p>}
            {!problemError && problemList?.length === 0 && <Notice>No conditions recorded for this patient.</Notice>}
            {!problemError && problemList && problemList.length > 0 && (
              <ul className="grid gap-3">
                {problemList.map((condition) => (
                  <li key={condition.id} className="rounded-lg border border-slate-200 p-3">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="text-sm font-medium text-slate-800">{condition.text ?? '(no text)'}</span>
                      <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-600">
                        Condition/{condition.id}
                      </span>
                    </div>
                    <div className="mt-1">
                      {(condition.codings ?? []).map((coding) => (
                        <CodingRow key={`${coding.system}-${coding.code}`} coding={coding} />
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        )}

        <footer className="pb-4 text-xs text-slate-400">
          NAMASTE {NAMASTE_SYSTEM} · ICD-11 {MMS_SYSTEM}
        </footer>
      </main>
    </div>
  )
}
