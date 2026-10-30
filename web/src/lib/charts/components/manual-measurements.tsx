import React, { useEffect, useId, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { RequestSession, type ManualWriteCommand, type RequestReceipt } from '../../goals/requestSessions';
import { motion, useReducedMotion } from 'motion/react';
import type { ChartBlock, ManualMeasureState, MeasureValue } from '../../goals/contracts';
import { isManualWriteConflict, manualAcceptedValue, manualConflictState, manualErrorMessage, manualMeasurementHttp, manualStateCanReplace, parseManualDraft, submitManualWrite, type ManualMeasurementAdapter } from '../../goals/manualMeasurements';
import { AnimatedCounter } from './animated-counter';
import { LineChart } from './line-chart-integrated';
import { motionTokens } from '../lib/motion-tokens';
import { SupportingMetric } from './supporting-metric';
import { Range } from '../GoalChart';
import { chartData, readChartPreference, selectedTarget, coverageLabel, readingSuffix } from '../presentation';
import './manual-measurements.css';

const blue = 'var(--accent-bright)', green = 'var(--accent-done)', red = 'var(--accent-error)';
const formatCompactNumber = (number: number) => new Intl.NumberFormat('en', { notation: 'compact' }).format(number);
const dateLabel = (date: string) => new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(`${date}T12:00:00Z`));
const dayBefore = (date: string, days: number) => { const value = new Date(`${date}T12:00:00Z`); value.setUTCDate(value.getUTCDate() - days); return value.toISOString().slice(0, 10); };
const arcFor = (fraction: number) => {
  const start = -135, end = start + 270 * Math.min(1, Math.max(.00001, fraction));
  const at = (angle: number) => `${50 + 42 * Math.sin(angle * Math.PI / 180)} ${50 - 42 * Math.cos(angle * Math.PI / 180)}`;
  return `M ${at(start)} A 42 42 0 ${end - start > 180 ? 1 : 0} 1 ${at(end)}`;
};

type InlineControl = { prepare(): Promise<boolean>; focus(): void };
type InlineProps = {
  value: number | undefined; state: ManualMeasureState | null; label: string; suffix?: string; decimals?: number;
  onSave(value: number, basis: ManualMeasureState): Promise<ManualMeasureState>; onCancel(): Promise<ManualMeasureState | null>; pending: boolean; blocked: boolean; cumulative: boolean;
  focusToken?: number; center?: boolean; control: React.Ref<InlineControl>; recovery?: RequestReceipt<ManualWriteCommand>; onEdit(): void;
};

/** Native input stays over the exact approved rolling counter. Drafts never become accepted values. */
function InlineValue({ value, state, label, suffix = '', decimals = 0, onSave, onCancel, pending, blocked, cumulative, focusToken = 0, center = false, control, recovery, onEdit }: InlineProps) {
  const format = (number: number | undefined) => number == null ? '' : String(number);
  const recovered = recovery?.command.operation === 'replace' ? recovery : undefined;
  const recoveredValue = recovery?.command.operation === 'replace' ? String(recovery.command.value) : format(value);
  const [draft, setDraft] = useState(recoveredValue), [error, setError] = useState(recovered?.status === 'failed' ? manualErrorMessage(recovered.error) : ''), [focused, setFocused] = useState(false), [saveFailed, setSaveFailed] = useState(recovered?.status === 'failed' && !(isManualWriteConflict(recovered.error)));
  const input = useRef<HTMLInputElement>(null), cancel = useRef(false), commitPromise = useRef<Promise<boolean> | null>(null);
  const basis = useRef(state), draftRef = useRef(draft), errorRef = useRef(error), focusedRef = useRef(false), id = useId();
  const accepted = useRef({ value, state }); accepted.current = { value, state };
  const previousLabel = useRef(label);
  const setText = (text: string) => { draftRef.current = text; setDraft(text); };
  const fail = (message: string, failedSave = false) => { errorRef.current = message; setError(message); setSaveFailed(failedSave); };
  useEffect(() => {
    if (label !== previousLabel.current || !focusedRef.current && !errorRef.current && !commitPromise.current) {
      setText(format(value)); basis.current = state; fail('');
    }
    previousLabel.current = label;
  }, [value, state, label]);
  useEffect(() => {
    if (recovery?.command.operation !== 'replace') return;
    setText(String(recovery.command.value));
    if (recovery.status === 'failed') fail(manualErrorMessage(recovery.error), !(isManualWriteConflict(recovery.error)));
  }, [recovery]);
  useEffect(() => { if (focusToken) { input.current?.focus(); input.current?.select(); } }, [focusToken]);
  function commit(explicitRetry = false): Promise<boolean> {
    if (commitPromise.current) return commitPromise.current;
    if (cancel.current) { cancel.current = false; return Promise.resolve(true); }
    if (blocked || errorRef.current && !explicitRetry) return Promise.resolve(false);
    if (draftRef.current === format(accepted.current.value)) { fail(''); return Promise.resolve(true); }
    const number = parseManualDraft(draftRef.current);
    if (number === null) { fail('Enter a number.'); return Promise.resolve(false); }
    const initial = basis.current ?? accepted.current.state;
    if (!initial) return Promise.resolve(false);
    const attempt = (async () => {
      try {
        const next = await onSave(number, initial);
        setText(format(manualAcceptedValue(next, cumulative))); fail(''); basis.current = next; cancel.current = true;
        return true;
      } catch (error) { fail(manualErrorMessage(error), !(isManualWriteConflict(error))); return false; }
    })();
    commitPromise.current = attempt;
    void attempt.finally(() => { commitPromise.current = null; });
    return attempt;
  }
  useImperativeHandle(control, () => ({
    async prepare() { const ok = await commit(); if (!ok) input.current?.focus(); return ok; },
    focus() { input.current?.focus(); },
  }));
  const numericText = String(value ?? 0), [mantissa, exponent = '0'] = numericText.toLowerCase().split('e');
  const precision = Math.max(decimals, (mantissa.split('.')[1]?.length ?? 0) - Number(exponent));
  const fractionDigits = Math.min(20, precision);
  const displayText = value == null ? '—' : precision > 20 ? String(value) : new Intl.NumberFormat('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: fractionDigits }).format(value);
  // Extremely small values retain their exact native-input text instead of a rounded zero.
  const showCounter = value != null && precision <= 20 && !focused && !error && draft === format(value);
  return <div data-counter-visible={showCounter || undefined} className={'inline-value' + (center ? ' centered' : '')} data-large={draft.length > 5 || undefined}>
    <div className="number-row"><span className="number-shell">
      <span className="number-sizer" aria-hidden="true">{draft || '—'}</span><span className="number-sizer" aria-hidden="true">{displayText}</span>
      <span className="counter-display" aria-hidden="true">{value != null && <AnimatedCounter value={value} decimals={fractionDigits} />}</span>
      <input ref={input} aria-label={label} aria-invalid={!!error} aria-describedby={error ? id : undefined} inputMode="decimal" enterKeyHint="done" autoComplete="off" spellCheck={false} placeholder="—" value={draft} data-reading={value ?? 'missing'} readOnly={pending || !state}
        onFocus={event => { focusedRef.current = true; setFocused(true); cancel.current = false; if (!errorRef.current) basis.current = accepted.current.state; event.currentTarget.select(); }}
        onChange={event => { onEdit(); if (errorRef.current) basis.current = accepted.current.state; setText(event.target.value); fail(''); }}
        onBlur={() => { focusedRef.current = false; setFocused(false); void commit(); }}
        onKeyDown={event => {
          if (event.key === 'Enter') { event.preventDefault(); void commit(true).then(ok => { if (ok) input.current?.blur(); }).catch(cause => fail(manualErrorMessage(cause), true)); }
          else if (event.key === 'Escape') { event.preventDefault(); if (pending) return; cancel.current = true; void onCancel().then(next => { setText(format(next ? manualAcceptedValue(next, cumulative) : accepted.current.value)); basis.current = next ?? accepted.current.state; fail(''); input.current?.blur(); }).catch(cause => fail(manualErrorMessage(cause), true)); }
        }} />
    </span>{suffix && <span className="unit">{suffix}</span>}</div>
    {error && <span id={id} className="error" role="alert">{error}{saveFailed && <button type="button" className="manual-retry" onClick={() => void commit(true)}>Retry</button>}</span>}
  </div>;
}

export function ManualMeasurement({ block, values, adapter: suppliedAdapter, narrow = false, compact = false, integrated = false, preferenceKey, session: suppliedSession }: { block: ChartBlock; values: MeasureValue[]; adapter?: ManualMeasurementAdapter; narrow?: boolean; compact?: boolean; integrated?: boolean; preferenceKey: string; session?: RequestSession<ManualWriteCommand> }) {
  const adapter = useMemo(() => suppliedAdapter ?? manualMeasurementHttp(block.measure_id!), [suppliedAdapter, block.measure_id]);
  const session = useMemo(() => suppliedSession ?? new RequestSession<ManualWriteCommand>(), [suppliedSession, block.measure_id]);
  const [recovery, setRecovery] = useState(session.current);
  const foldable = block.placement === 'supporting' && narrow;
  const [preferences, setPreferences] = useState(() => { const saved = readChartPreference(preferenceKey); return block.ranges?.length && !block.ranges.some(range => range.id === saved.range) ? { ...saved, range: block.ranges[0].id } : saved; });
  const host = useRef<HTMLElement>(null), focusInside = useRef(false);
  const preference = (patch: Partial<typeof preferences>) => setPreferences(previous => { const next = { ...previous, ...patch }; try { sessionStorage.setItem(preferenceKey, JSON.stringify(next)); } catch {} return next; });
  useLayoutEffect(() => { if (foldable && !preferences.expanded && focusInside.current) host.current?.querySelector<HTMLButtonElement>('.support-summary')?.focus(); }, [foldable, preferences.expanded]);
  const cumulative = block.manual!.model === 'cumulative_target', reading = block.manual!.model === 'individual_readings';
  const [state, setState] = useState<ManualMeasureState | null>(null), stateRef = useRef<ManualMeasureState | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | undefined>(session.current?.command.date), selectedRef = useRef<string | undefined>(session.current?.command.date);
  const [acceptedHistory, setAcceptedHistory] = useState<Map<string, MeasureValue>>(new Map()), acceptedHistoryRef = useRef(new Map<string, MeasureValue>());
  const history = useMemo(() => [...values.filter(value => value.measure_id !== block.measure_id || !acceptedHistory.has(value.date)), ...acceptedHistory.values()].sort((a, b) => a.date.localeCompare(b.date)), [values, acceptedHistory, block.measure_id]);
  const [readRefreshVersion, setReadRefreshVersion] = useState(0), refreshSkipped = useRef(false);
  const [pending, setPending] = useState(session.current?.status === 'pending'), [error, setError] = useState(session.current?.status === 'failed' ? manualErrorMessage(session.current.error) : ''), [focusToken, setFocusToken] = useState(0);
  const retryRequest = useRef<ManualWriteCommand | null>(session.current?.command ?? null), pendingPromise = useRef<Promise<void> | null>(null), readEpoch = useRef(0), selectionPending = useRef(false);
  const actionTail = useRef<Promise<void>>(Promise.resolve());
  const runAction = (action: () => Promise<void>) => { const next = actionTail.current.then(action); actionTail.current = next.catch(() => {}); return next; };
  const control = useRef<InlineControl>(null), reduce = useReducedMotion();
  const rememberObservation = (next: ManualMeasureState) => {
    const observation = next.observation;
    const updated = new Map(acceptedHistoryRef.current);
    updated.set(next.date, { measure_id: next.measure_id, date: next.date, definition_revision: next.definition_revision,
      status: observation ? 'value' : 'missing_input', value: observation?.value ?? null, inputs: [], missing_inputs: [],
      query_start_at: observation?.query_start_at ?? null, query_end_at: observation?.query_end_at ?? null, limitation: observation?.limitation ?? null,
      coverage_status: observation?.coverage_status ?? 'not_applicable',
      coverage_reasons: observation?.coverage_reasons ?? [] });
    acceptedHistoryRef.current = updated; setAcceptedHistory(updated);
  };
  const accept = (next: ManualMeasureState, date?: string) => {
    if (!manualStateCanReplace(stateRef.current, next, date)) return false;
    stateRef.current = next; setState(next); rememberObservation(next);
    if (cumulative) { selectedRef.current = next.date; setSelectedDate(next.date); }
    return true;
  };
  const refresh = async () => {
    const selected = selectedRef.current, date = cumulative ? undefined : selected, epoch = ++readEpoch.current;
    const next = await adapter.read(date);
    if (epoch === readEpoch.current && selectedRef.current === selected) accept(next, date);
    return stateRef.current;
  };
  useEffect(() => {
    if (pending || pendingPromise.current || selectionPending.current) { refreshSkipped.current = true; return; }
    refreshSkipped.current = false;
    const selected = selectedRef.current, date = cumulative ? undefined : selected, epoch = ++readEpoch.current;
    const previousDates = cumulative ? [] : [...acceptedHistoryRef.current.keys()].filter(key => key !== date);
    void Promise.all([adapter.read(date), ...previousDates.map(key => adapter.read(key))]).then(([next, ...retained]) => {
      if (epoch !== readEpoch.current || selectedRef.current !== selected || !accept(next, date)) return;
      if (!selectedRef.current) { selectedRef.current = next.date; setSelectedDate(next.date); }
      for (const snapshot of retained) if (manualStateCanReplace(stateRef.current, snapshot, snapshot.date)) rememberObservation(snapshot);
    }).catch(cause => { if (epoch === readEpoch.current) setError(manualErrorMessage(cause)); });
    return () => { readEpoch.current++; };
  }, [adapter, values, cumulative, pending, selectedDate, readRefreshVersion]);

  useEffect(() => {
    const sync = () => {
      const receipt = session.current;
      setRecovery(receipt); setPending(receipt?.status === 'pending');
      retryRequest.current = receipt?.command ?? null;
      setError(receipt?.status === 'failed' ? manualErrorMessage(receipt.error) : '');
      const current = receipt?.status === 'failed' ? manualConflictState(receipt.error) : undefined;
      if (current) accept(current, receipt?.command.date);
    };
    sync();
    return session.subscribe(sync);
  }, [session]);

  async function send(body: ManualWriteCommand): Promise<void> {
    if (pendingPromise.current) return pendingPromise.current;
    setPending(true); setError(''); retryRequest.current = body; readEpoch.current++;
    const attempt = (async () => {
      try {
        const result = await submitManualWrite(session, body, adapter);
        // The acknowledgement can describe an old replay. Only coherent current state drives the chart.
        readEpoch.current++;
        accept(result.state, result.receipt.date);
        retryRequest.current = null;
      } catch (cause) {
        readEpoch.current++;
        const current = manualConflictState(cause); if (current) accept(current, session.current?.command.date ?? body.date);
        setError(manualErrorMessage(cause)); throw cause;
      } finally { setPending(false); pendingPromise.current = null; }
    })();
    pendingPromise.current = attempt;
    return attempt;
  }
  async function replacement(value: number, basis: ManualMeasureState) {
    const retry = retryRequest.current;
    if (session.current?.status === 'failed' && isManualWriteConflict(session.current.error)) throw session.current.error;
    if (retry?.operation === 'replace' && retry.value === value) { await send(retry); return stateRef.current!; }
    // The session owns the draft before the action-local date read can suspend.
    await send({ request_id: crypto.randomUUID(), date: basis.date, expected_definition_revision: basis.definition_revision,
      basis: cumulative ? 'latest' : 'date', operation: 'replace', value, expected_write_revision: basis.write_revision,
      ...(cumulative ? { preflight: true } : {}) });
    return stateRef.current!;
  }
  function select(date: string, focus = false) { return runAction(async () => {
    if (!await control.current?.prepare() || retryRequest.current) return;
    const epoch = ++readEpoch.current; selectionPending.current = true;
    try {
      const next = await adapter.read(date);
      if (epoch === readEpoch.current && accept(next, date)) {
        readEpoch.current++; selectedRef.current = date; setSelectedDate(date);
        if (focus) setFocusToken(value => value + 1);
      }
    } catch (cause) { setError(manualErrorMessage(cause)); }
    finally { selectionPending.current = false; if (refreshSkipped.current) setReadRefreshVersion(value => value + 1); }
  }); }
  function increment(delta: number) { return runAction(async () => {
    if (!await control.current?.prepare() || retryRequest.current) return;
    const basis = stateRef.current; if (!basis) return;
    try {
      await send({ request_id: crypto.randomUUID(), date: basis.date, expected_definition_revision: basis.definition_revision,
        basis: cumulative ? 'latest' : 'date', operation: 'increment', delta, ...(cumulative ? { preflight: true } : {}) });
    } catch (cause) { setError(manualErrorMessage(cause)); }
  }); }
  const current = state ? manualAcceptedValue(state, cumulative) : undefined, target = selectedTarget(block, preferences.range)!;
  const today = state?.current_date ?? history.at(-1)?.date;
  const dates = block.ranges?.length ? chartData(block, history, preferences.range).map(datum => datum.key) : today ? Array.from({ length: 7 }, (_, index) => dayBefore(today, 6 - index)) : [];
  const readings = dates.map(date => history.find(item => item.measure_id === block.measure_id && item.date === date)?.value ?? undefined);
  if (state?.observation && dates.includes(state.date)) readings[dates.indexOf(state.date)] = state.observation.value;
  const minimum = Math.min(0, target ?? 0, ...readings.filter((item): item is number => item !== undefined));
  const maximum = Math.max(target ?? 0, 0, ...readings.map(item => item ?? 0)), span = maximum - minimum || 1;
  const selected = selectedDate ?? today, title = block.title ?? 'Reading';
  const numberText = (number: number) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 20 }).format(number);
  const suffix = readingSuffix(block);
  const inline = <InlineValue control={control} value={current} state={state} onSave={replacement} cumulative={cumulative} onCancel={async () => {
    if (retryRequest.current) { const next = await refresh(); session.discard(); retryRequest.current = null; setError(''); return next; }
    return stateRef.current;
  }} recovery={recovery} onEdit={() => { if (session.current?.status === 'failed') { session.discard(); retryRequest.current = null; setError(''); } }} pending={pending} blocked={!!retryRequest.current && retryRequest.current.operation === 'increment'}
    label={cumulative ? title : `${title} for ${selected ? dateLabel(selected) : 'selected date'}`} suffix={reading ? suffix : ''} decimals={block.format?.decimals ?? 0} center={cumulative} focusToken={focusToken} />;
  const adds = <div className="add-buttons">{block.manual!.increments?.map((amount, index) => <button type="button" key={`${index}:${amount}`} disabled={!state} aria-busy={pending || undefined} onClick={() => void increment(amount)}>{amount > 0 ? '+' : ''}{amount}</button>)}</div>;
  const acceptedObservation = cumulative ? state?.latest_observation : state?.observation;
  const coverage = coverageLabel(acceptedObservation?.coverage_status ?? 'not_applicable');
  const comparison = current !== undefined && coverage ? `${coverage}${target === undefined ? '' : ` · ${coverage === 'Partial reading' ? 'Partial difference' : 'Difference (coverage unavailable)'}: ${numberText(current - target)}${suffix}`}` : current === undefined ? 'No reading' : target === undefined ? '' : reading
    ? current >= target ? `${numberText(target)}${suffix} target reached` : `${(target - current).toFixed(block.format?.decimals ?? 0)}${suffix} to a ${numberText(target)}${suffix} ${(block.series?.[0]?.name ?? 'Reading').toLowerCase()}`
    : current < target ? `${numberText(target - current)} to daily target` : current === target ? 'Daily target reached' : `${numberText(current - target)} above daily target`;
  const range = block.ranges?.length ? <div className="chart-range-footer"><Range block={block} value={preferences.range} id={preferenceKey} onChange={range => { void runAction(async () => {
    if (!await control.current?.prepare() || retryRequest.current) return;
    const date = chartData(block, history, range).at(-1)?.key;
    if (date && !cumulative) {
      const epoch = ++readEpoch.current; selectionPending.current = true;
      try { const next = await adapter.read(date); if (epoch !== readEpoch.current || !accept(next, date)) return; readEpoch.current++; selectedRef.current = date; setSelectedDate(date); }
      catch (cause) { setError(manualErrorMessage(cause)); return; }
      finally { selectionPending.current = false; if (refreshSkipped.current) setReadRefreshVersion(value => value + 1); }
    }
    preference({ range });
  }); }} /></div> : null;
  const card = <article className="manual-measurement" data-embedded={foldable || integrated || undefined} data-compact={compact || block.placement === 'supporting' || undefined} data-manual-model={block.manual!.model} aria-busy={pending || undefined}>
    {!foldable && <div className="card-heading"><h3>{title}</h3>{!cumulative && selected && selected !== today && <span className="selected-date">{dateLabel(selected)}</span>}</div>}
    {foldable && !cumulative && selected && selected !== today && <div className="selected-date embedded-date">{dateLabel(selected)}</div>}
    {cumulative ? <><div className="radial"><svg viewBox="0 0 100 90" role="meter" aria-label={title} aria-valuemin={Math.min(0, current ?? 0)} aria-valuemax={target} aria-valuenow={Math.min(current ?? 0, target)} aria-valuetext={current === undefined ? 'No reading' : `${current} of ${target}${coverage ? `, ${coverage}` : current >= target ? ', target reached' : ''}`}>
      <path className="radial-track" d={arcFor(1)} /><motion.path className="radial-fill" d={arcFor(1)} pathLength="1" initial={false} animate={{ strokeDasharray: `${Math.max(0, Math.min(1, (current ?? 0) / target))} 1`, stroke: coverage ? 'var(--text-muted)' : current !== undefined && current >= target ? green : blue, opacity: current === undefined || current <= 0 ? 0 : 1 }} transition={reduce ? { duration: 0 } : motionTokens.spring.smooth} />
    </svg><div className="radial-center">{inline}<span className="denominator">of {numberText(target)}</span></div></div><div className="radial-footer"><span className="completion" style={{ color: coverage ? 'var(--text-muted)' : green }} role="status">{coverage ? comparison : current === target ? 'Target reached' : current !== undefined && current > target ? `${numberText(current - target)} beyond target` : ''}</span>{adds}</div></> : <>
      <div className="reading-row">{inline}{!reading && adds}</div><p className="comparison" style={{ color: current === undefined ? 'var(--text-faintest)' : coverage ? 'var(--text-muted)' : current >= target ? green : reading ? 'var(--text-faint)' : red }}>{comparison}</p>
      {reading ? <div className="reading-chart"><LineChart data={dates.map((date, index) => ({ key: date, label: dateLabel(date), axisLabel: dateLabel(date), values: { actual: readings[index], target } }))}
        series={[{ key: 'actual', label: block.series?.[0]?.name ?? 'Reading', color: blue, area: true }, ...(target === undefined ? [] : [{ key: 'target', label: `${numberText(target)}${suffix} target`, color: 'var(--text-faintest)', dashed: true, area: false }])]}
        label={title} height={196} domain={[minimum, maximum]} formatValue={value => `${value.toFixed(block.format?.decimals ?? 0)}${suffix}`} onSelect={index => void select(dates[index], true)} /></div>
        : <div className="bar-chart" data-maximum={maximum}><div className="bar-grid" aria-hidden="true">
          {(maximum - target) / span > .09 && <span style={{ bottom: '100%' }}>{formatCompactNumber(maximum)}</span>}
          <span className="target" style={{ bottom: (target - minimum) / span * 100 + '%' }}>{(target - minimum) / span > .08 ? formatCompactNumber(target) : ''}</span><span style={{ bottom: -minimum / span * 100 + '%' }}>0</span>
          {minimum < 0 && <span style={{ bottom: 0 }}>{formatCompactNumber(minimum)}</span>}
        </div><div className="bars" style={{ gridTemplateColumns: `repeat(${Math.max(1, dates.length)}, minmax(0, 1fr))` }} role="group" aria-label={`${title} by date`}>
          {readings.map((value, index) => <button type="button" key={dates[index]} className="bar-pick" aria-pressed={selected === dates[index]} aria-label={`${dateLabel(dates[index])}, ${value == null ? 'no reading' : `${value} ${title.toLowerCase()}`}`} onClick={() => void select(dates[index])}>
            <span className="bar-space"><motion.span className={'bar ' + (value == null ? 'missing' : '')} initial={false}
              style={minimum < 0 ? { position: 'absolute', bottom: (Math.min(0, value ?? 0) - minimum) / span * 100 + '%' } : undefined}
              animate={{ height: value == null || value === 0 ? 2 : Math.abs(value) / span * 100 + '%', backgroundColor: value == null ? 'var(--text-faintest)' : coverageLabel(history.find(item => item.measure_id === block.measure_id && item.date === dates[index])?.coverage_status ?? 'not_applicable') ? 'var(--text-muted)' : value >= target ? green : red }} transition={{ duration: reduce ? 0 : motionTokens.duration.standard }} /></span>
            <span className="bar-date">{dates[index] === today ? 'Today' : dateLabel(dates[index]).split(' ')[0]}</span>
          </button>)}
        </div></div>}
    </>}
    {range}
    {error && retryRequest.current?.operation !== 'replace' && <div className="manual-write-error error" role="alert">{error}<button type="button" disabled={pending} onClick={() => { const retry = retryRequest.current; if (retry && !(isManualWriteConflict(session.current?.error))) void send(retry).catch(() => {}); else void refresh().then(() => { session.discard(); setError(''); }).catch(cause => setError(manualErrorMessage(cause))); }}>Retry</button></div>}
  </article>;
  return <section ref={host} onFocusCapture={() => { focusInside.current = true; }} onBlurCapture={event => { if (event.relatedTarget && !host.current?.contains(event.relatedTarget as Node)) focusInside.current = false; }}>
    {foldable ? <SupportingMetric title={title} reading={current === undefined ? '—' : <AnimatedCounter value={current} decimals={block.format?.decimals ?? 0} suffix={suffix} />} status={comparison} open={preferences.expanded} onToggle={() => { void runAction(async () => { if (preferences.expanded && !await control.current?.prepare()) return; preference({ expanded: !preferences.expanded }); }); }}>{card}</SupportingMetric> : card}
  </section>;
}
