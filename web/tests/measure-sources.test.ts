import { describe, expect, it } from 'vitest';
import { queries } from '../src/lib/queryCatalogue';
import { parseShellDestination } from '../src/lib/shellDestination';
import { clearSecretFields, emptySecretFields, secretBody } from '../src/lib/measureSources/credentialForm';
import { failureLabel, fixedWriteError, runRevision } from '../src/lib/measureSources/presentation';
import { PlannerFetchError } from '../src/lib/api';
import type { SourceStatus } from '../src/lib/measureSources/wire';
const status = (): SourceStatus => ({
  source: { id:'s', name:'Visits', scope:{project_id:'p',account:'Production'}, responsible:{kind:'chief',id:'chief'}, source_revision:2, enabled:true, local_time:'09:00', active_script_revision:1 },
  health:'failed', latest_run:{id:'test',mode:'test',status:'succeeded',created_at:1,finished_at:2,failure:null,observation_count:1}, latest_success_at:1, latest_values:[],output_count:0,
  failure_episode:{source_id:'s',episode:1,opened_at:1,latest_run_id:'failed-collection',closed_at:null},
  collection_failure:{code:'http_error',http_status:401,budget:null,measure_id:null,date:null,exception_type:null,source_line:null}, running_run_id:null
});
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
  it('keeps collection failure and revision-bound controls independent from test outcomes', () => {
    const value=status();
    expect(failureLabel(value.collection_failure)).toContain('rejected the credential');
    expect(failureLabel(value.latest_run?.failure ?? null)).toBe('');
    expect(runRevision(value)).toBe(1);
    value.running_run_id='r'; expect(runRevision(value)).toBeNull();
    value.running_run_id=null; value.latest_run!.status='pending'; expect(runRevision(value)).toBeNull();
    value.latest_run=null; value.source.enabled=false; expect(runRevision(value)).toBe(1); value.source.active_script_revision=null; expect(runRevision(value)).toBeNull();
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
