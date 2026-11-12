export interface ManagedSkill {
  name: string;
  description: string;
  markdown_body: string;
  source_text: string;
}
export interface SkillPatch {
  description?: string;
  markdown_body?: string;
}
export interface SkillOptions {
  now: number;
  database_parent: string;
  allow_create?: boolean;
}
export interface SkillSaveIncompleteDetail {
  skill_name: string;
  saved: true;
  published: boolean;
  phase: 'file' | 'history';
}
export interface StaleReference {
  skill_name: string;
  kind: string;
  reference: string;
}
export interface CommandNode {
  [command: string]: CommandNode | null;
}
