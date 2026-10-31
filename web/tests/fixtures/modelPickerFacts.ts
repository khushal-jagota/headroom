export const pickerModels: import("../../src/lib/conversation/wire").BackendModel[] = [
  {enabled: true, model_id: "opus", resolved_model_id: "claude-opus-5", display_name: "Opus 5", reasoning_effort_options: ["high"]},
  {enabled: true, model_id: "claude-opus-5", resolved_model_id: "claude-opus-5", display_name: "Opus 5", reasoning_effort_options: ["high"]},
  ...Array.from({length: 12}, (_, index) => ({enabled: true, model_id: `exact-concrete-id-with-long-context-${index}`,
    display_name: `Retained catalog choice ${index}`, reasoning_effort_options: ["high"]}))
];
