/** Base class for Python ValueError-compatible failures across server modules. */
export class ValueError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ValueError';
  }
}
