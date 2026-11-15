import {fetchJson} from '../api';
import type {ChiefManagementSettings} from '../types';
type ChiefConversationReference=Pick<ChiefManagementSettings,'conversation_id'>;
import {recallOutgoingMessages,rememberOutgoingMessages,type OutgoingMessage} from '../conversation/outgoing';

export const chiefDraftKey='owner:chief';
// Attachments use the Review draft store. Text shares Chief's existing composer key.
export const chiefAttachmentDraftId='owner:chief';
const attemptKey='review-chief-attempt';
const destinationKey='panels:review-chief-attempt-destination';
export interface ChiefMessageAttempt {
  message:OutgoingMessage;
  expectedConversationId:string|null;
}
interface StoredDestination {messageId:string;conversationId:string|null;}

export function readChiefPointer():Promise<ChiefConversationReference>{
  return fetchJson<ChiefConversationReference>('/api/chief/conversation');
}
export async function readChiefAttempt():Promise<ChiefMessageAttempt|null>{
  const raw=window.sessionStorage.getItem(destinationKey);
  const messages=await recallOutgoingMessages(attemptKey);
  if(!messages.length){
    if(raw!==null)throw new Error('The saved delivery could not be read. Your draft is kept. Open Chief before you send again.');
    return null;
  }
  const destination:unknown=raw===null?null:JSON.parse(raw);
  if(!destination || typeof destination!=='object' || !('messageId' in destination) || !('conversationId' in destination)
    || destination.messageId!==messages[0].messageId
    || !(destination.conversationId===null || typeof destination.conversationId==='string')){
    throw new Error('The saved message has no confirmed destination. Open Chief before you send it again.');
  }
  return {message:messages[0],expectedConversationId:destination.conversationId};
}
function writeDestination(value:string|null):void{
  if(value===null)window.sessionStorage.removeItem(destinationKey);
  else window.sessionStorage.setItem(destinationKey,value);
  if(window.sessionStorage.getItem(destinationKey)!==value)throw new Error('The message destination could not be saved in this browser.');
}
export async function saveChiefAttempt(attempt:ChiefMessageAttempt):Promise<void>{
  const destination:StoredDestination={messageId:attempt.message.messageId,conversationId:attempt.expectedConversationId};
  // Preserve an existing unresolved attempt if this is a destination update.
  const previous=window.sessionStorage.getItem(destinationKey);
  try{
    writeDestination(JSON.stringify(destination));
    if(!await rememberOutgoingMessages(attemptKey,[attempt.message])){
      throw new Error('The message could not be saved in this browser. Your draft is kept.');
    }
    const recalled=await recallOutgoingMessages(attemptKey);
    const stored=recalled[0];
    if(recalled.length!==1 || !stored || stored.messageId!==attempt.message.messageId
      || stored.sentAtUnixMilliseconds!==attempt.message.sentAtUnixMilliseconds
      || JSON.stringify(stored.content)!==JSON.stringify(attempt.message.content)){
      throw new Error('The message could not be saved in this browser. Your draft is kept.');
    }
  }catch(problem){
    // This save did not authorize a POST. Roll back new metadata so an unsent draft
    // does not become an unreadable pending delivery after a reload.
    try{writeDestination(previous);}catch{
      throw new Error('The browser could not save the message or clear its saved destination. Your draft is kept.');
    }
    throw problem;
  }
}
export async function clearChiefAttempt():Promise<void>{
  if(!await rememberOutgoingMessages(attemptKey,[]))throw new Error('The browser could not clear the saved delivery. Check delivery before you send again.');
  if((await recallOutgoingMessages(attemptKey)).length!==0)throw new Error('The saved delivery could not be cleared. Check delivery before you send again.');
  window.sessionStorage.removeItem(destinationKey);
  if(window.sessionStorage.getItem(destinationKey)!==null)throw new Error('The saved destination could not be cleared.');
}
