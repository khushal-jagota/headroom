import { createHash } from 'node:crypto';
import { frontmatter_bounds } from '../skills/logic.ts';
import type { WorkflowDefinition, WorkerTypeDefinition, GuidanceConversionReport, GuidanceFragment } from './contracts.ts';
export function render_workflow_guide(definition:WorkflowDefinition):string {
  const sections=[`# ${definition.label}`];
  if (definition.guidance) sections.push(definition.guidance);
  for (const stage of definition.stages) {
    sections.push(`## ${stage.label}\n\nStage: \`${stage.id}\`. Owner: ${stage.ownership_mode || 'terminal'}. Result: \`${stage.gating_field || 'none'}\`.`);
    if (stage.instructions) sections.push(stage.instructions);
  }
  return sections.join('\n\n')+'\n';
}
export function guidance_conversion_report(definition:WorkerTypeDefinition,source:string):GuidanceConversionReport {
  const [,body]=frontmatter_bounds(source);
  const aliases=new Map<string,string>();
  for (const stage of definition.stages) aliases.set(stage.id.toLowerCase(),stage.id);
  for (const stage of definition.stages) aliases.set(stage.label.toLowerCase(),stage.id);
  const sortedAliases=[...aliases.keys()].sort((a,b)=>b.length-a.length);
  const matched_stage=(text:string):string|null=>{
    const normalized=text.replaceAll('\\','').replaceAll('`','').replaceAll('**','').trim().toLowerCase();
    for (const alias of sortedAliases) {
      const escaped=alias.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
      if (normalized===alias || new RegExp(`^${escaped}\\s+[—–-]\\s+.+$`).test(normalized)) return aliases.get(alias)!;
    }
    return null;
  };
  const lines=body.match(/[^\r\n]*(?:\r\n|\r|\n|$)/g)?.filter(Boolean) ?? [];
  const fragments:GuidanceFragment[]=[];
  let target='guidance',section_kind='global',stage_heading_depth=0,fence_character='',fence_length=0;
  for (const line of lines) {
    const fence=/^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (fence_character) {
      if (fence && fence[1][0]===fence_character && fence[1].length>=fence_length && !line.slice(fence[0].length).trim()) {fence_character='';fence_length=0;}
    } else if (fence) {fence_character=fence[1][0];fence_length=fence[1].length;}
    else {
      const heading=/^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line.replace(/[\r\n]+$/,''));
      const bullet=/^[-*]\s+\*\*(.+?)\*\*/.exec(line);
      if (heading) {
        const depth=heading[1].length,matched=matched_stage(heading[2]);
        if (matched) {target=matched;section_kind='heading';stage_heading_depth=depth;}
        else if (section_kind!=='heading'||depth<=stage_heading_depth) {target='guidance';section_kind='global';}
      } else if (section_kind!=='heading') {
        if (bullet) {
          const matched=matched_stage(bullet[1]);
          if (matched) {target=matched;section_kind='bullet';}
          else if (section_kind==='bullet') {target='guidance';section_kind='global';}
        } else if (section_kind==='bullet' && /^\S/.test(line)) {target='guidance';section_kind='global';}
      }
    }
    if (fragments.length && fragments.at(-1)!.target===target) fragments.at(-1)!.text+=line;
    else fragments.push({target,text:line});
  }
  const digest=(s:string)=>createHash('sha256').update(s).digest('hex');
  return {skill_sha256:digest(source),body_sha256:digest(body),fragments,preserved_body_bytes:Buffer.byteLength(body)};
}
export function convert_existing_skill(definition:WorkerTypeDefinition,source:string):WorkerTypeDefinition {
  if (definition.guidance || definition.stages.some(s=>s.instructions)) return definition;
  const text=new Map<string,string>();
  for (const fragment of guidance_conversion_report(definition,source).fragments) text.set(fragment.target,(text.get(fragment.target) ?? '')+fragment.text);
  return {...definition,guidance:text.get('guidance') ?? '',stages:definition.stages.map(stage=>({...stage,instructions:text.get(stage.id) ?? ''}))};
}
