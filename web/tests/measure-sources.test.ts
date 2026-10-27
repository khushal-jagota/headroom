import { afterEach, describe, expect, it, vi } from 'vitest';
import { queries } from '../src/lib/queryCatalogue';
import { parseShellDestination } from '../src/lib/shellDestination';
import { clearSecretFields, emptySecretFields, secretBody } from '../src/lib/measureSources/credentialForm';
import { armStatusBoundary, failureLabel, fixedWriteError, runRevision } from '../src/lib/measureSources/presentation';
import { PlannerFetchError } from '../src/lib/api';
import type { SourceStatus } from '../src/lib/measureSources/wire';
const status = (): SourceStatus => ({
  source: { id:'s', name:'Visits', scope:{project_id:'p',account:'Production'}, responsible:{kind:'chief',id:'chief'}, source_revision:2, enabled:true, local_time:'09:00', active_script_revision:1, pending_script_revision:2, repair_ticket_id:null },
  health:'failed', latest_run:{id:'test',mode:'test',status:'succeeded',created_at:1,finished_at:2,failure:null,observation_count:1}, latest_success_at:1, latest_values:[],output_count:0,
  failure_episode:{source_id:'s',episode:1,opened_at:1,latest_run_id:'failed-collection',closed_at:null},
  collection_failure:{code:'http_error',http_status:401,budget:null,measure_id:null,date:null}, running_run_id:null, next_status_change_at:null
});
afterEach(() => vi.useRealTimers());
describe('measure source settings', () => {
  it('uses the existing standalone and workspace settings route with bounded distinct query keys', () => {
    expect(parseShellDestination('#/settings/measure-sources')).toMatchObject({name:'settings',params:{section:'measure-sources'}});
    expect(parseShellDestination('#/workspace/utility/settings/measure-sources').name).toBe('workspace');
    const a=queries.measureSources('p /x',0), b=queries.measureSources('p /x',20), c=queries.measureSources('q',0);
    expect(a.queryKey).not.toEqual(b.queryKey); expect(a.queryKey).not.toEqual(c.queryKey);
    expect(a.meta?.resourceKey).toBe('/api/measure-sources?limit=20&offset=0&project_id=p%20%2Fx');
    expect(queries.sourceCredentials('p','a',0).queryKey).not.toEqual(queries.sourceCredentials('p','b',0).queryKey);
    expect(queries.measureSourceRuns('s',10).meta?.resourceKey).toContain('limit=10&offset=10');
  });
  it('keeps collection failure and revision-bound controls independent from draft outcomes', () => {
    const value=status();
    expect(failureLabel(value.collection_failure)).toContain('rejected the credential');
    expect(failureLabel(value.latest_run?.failure ?? null)).toBe('');
    expect(runRevision(value,'collection')).toBe(1); expect(runRevision(value,'test')).toBe(2);
    value.running_run_id='r'; expect(runRevision(value,'collection')).toBeNull();
    value.running_run_id=null; value.latest_run!.status='pending'; expect(runRevision(value,'test')).toBeNull();
    value.latest_run=null; value.source.enabled=false; expect(runRevision(value,'collection')).toBeNull(); value.source.pending_script_revision=null; expect(runRevision(value,'test')).toBeNull();
  });
  it('uses one exact clock boundary and cancels it on scope change or destruction', () => {
    vi.useFakeTimers(); vi.setSystemTime(1000);
    const refresh=vi.fn(), a=status(), b=status(); a.next_status_change_at=3; b.next_status_change_at=2;
    const cancel=armStatusBoundary([a,b],refresh);
    expect(vi.getTimerCount()).toBe(1); vi.advanceTimersByTime(1000); expect(refresh).not.toHaveBeenCalled();
    cancel(); vi.advanceTimersByTime(1000); expect(refresh).not.toHaveBeenCalled();
    armStatusBoundary([a],refresh); vi.advanceTimersByTime(50); expect(refresh).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
  it('clears all credential variants and never renders response text in write errors', () => {
    const fields=emptySecretFields(); Object.assign(fields,{token:'secret',headerName:'X-Api-Key',value:'key',username:'name',password:'pass'});
    expect(secretBody('header',fields)).toEqual({header_name:'X-Api-Key',value:'key'});
    expect(secretBody('basic',fields)).toEqual({username:'name',password:'pass'});
    fields.password=''; expect(secretBody('basic',fields)).toEqual({username:'name',password:''});
    clearSecretFields(fields); expect(Object.values(fields).every(value=>value==='')).toBe(true);
    expect(fixedWriteError(new PlannerFetchError('secret','validation',{detail:'secret'}))).not.toContain('secret');
    expect(fixedWriteError(new PlannerFetchError('secret','conflict',{status:409}))).toContain('Refresh');
  });
});
