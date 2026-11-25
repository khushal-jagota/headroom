import { app, dialog } from 'electron';

/** False means Electron will quit this copy and relaunch the app from Applications. */
export async function prepareApplicationLocation(): Promise<boolean> {
  if (process.platform !== 'darwin' || !app.isPackaged || app.isInApplicationsFolder()) return true;

  const { response } = await dialog.showMessageBox({
    type: 'question',
    message: 'Move Panels to Applications?',
    detail: 'Install Panels in Applications for normal Mac app updates.',
    buttons: ['Move to Applications', 'Not Now'],
    defaultId: 0,
    cancelId: 1,
  });
  if (response !== 0) return true;

  try {
    // Electron copies, quits, and relaunches on success. A canceled authorization returns false.
    return !app.moveToApplicationsFolder();
  } catch (error) {
    await dialog.showMessageBox({
      type: 'error',
      message: 'Panels could not move to Applications.',
      detail: `${error instanceof Error ? error.message : String(error)}\n\nYou can quit Panels and drag it into Applications from the disk image.`,
      buttons: ['Continue'],
    });
    return true;
  }
}
