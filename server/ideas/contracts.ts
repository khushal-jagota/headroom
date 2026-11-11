export interface Idea {
  id: string; title: string; body: string; project_id: string | null; project: string | null;
  created_at: number; updated_at: number;
}
export interface CreateIdeaBody { title: string; body: string; project_id: string | null }
