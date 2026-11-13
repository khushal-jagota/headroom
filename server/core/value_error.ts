/** Base class for Python ValueError-compatible failures across server modules. */
export class ValueError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ValueError';
  }
}
/** Native/platform bridges retain Python's named ValueError convention. */
export function is_value_error(error: unknown): error is Error {
  return error instanceof ValueError || (error instanceof Error && error.name === 'ValueError');
}
