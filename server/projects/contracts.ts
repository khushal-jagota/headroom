export interface Project {
  id: string;
  name: string;
  summary: string;
  folder_path: string | null;
  repository_url: string | null;
  default_branch: string | null;
  created_at: number;
  updated_at: number;
}
export interface CreateProjectBody {
  name: string;
  summary: string;
  folder_path?: string | null;
  repository_url?: string | null;
  default_branch?: string | null;
}
export interface UpdateProjectBody {
  name?: string;
  summary?: string;
  folder_path?: string | null;
  repository_url?: string | null;
  default_branch?: string | null;
}
