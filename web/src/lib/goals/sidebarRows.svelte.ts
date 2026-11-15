const KEY='panels.goals-sidebar-rows';
function stored():boolean{try{return localStorage.getItem(KEY)==='1';}catch{return false;}}
export const goalSidebarRows=$state({on:stored()});
export function setGoalSidebarRows(on:boolean):void{goalSidebarRows.on=on;try{localStorage.setItem(KEY,on?'1':'0');}catch{/* The preference still works for this visit. */}}
