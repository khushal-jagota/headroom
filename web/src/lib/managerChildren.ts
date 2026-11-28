/** Groups that need the owner start open; every other group starts closed. A reader's choice takes precedence. */
const OPEN_BY_DEFAULT = ['awaiting_approval', 'awaiting_answer', 'assigned'];
export const managerGroupDefaultOpen = (key:string):boolean => OPEN_BY_DEFAULT.includes(key);
