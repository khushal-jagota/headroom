/** Where "Back to Panels" goes: the last address outside Settings in this tab. */
let placeOutsideSettings = "#/workspace";

export function rememberPlace(hash: string): void {
  if (!/^#\/settings(?:[/?]|$)/.test(hash)) placeOutsideSettings = hash;
}

export function returnPlace(): string {
  return placeOutsideSettings;
}
