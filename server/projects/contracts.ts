export interface Project {
  id: string;
  name: string;
  summary: string;
  folder_path: string | null;
  created_at: number;
  updated_at: number;
}
export interface CreateProjectBody {
  name: string;
  summary: string;
  folder_path?: string | null;
}
export interface UpdateProjectBody {
  name?: string;
  summary?: string;
  folder_path?: string | null;
}
