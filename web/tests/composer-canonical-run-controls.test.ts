import { expect, it, vi } from "vitest";
import { sendBodyFor } from "../src/lib/conversation/composer";
import { mintOutgoingMessage, rememberOutgoingMessages, recallOutgoingMessages, resetOutgoingImageReservationsForTest } from "../src/lib/conversation/outgoing";

// The outgoing store is the actual durable browser boundary used after transport
// uncertainty. Pure draft tests cannot prove its decoder retains explicit null.
it("reopens an uncertain outgoing send with the same exact run intent and old absent fields", async () => {
  const stored = new Map<string, string>();
  vi.stubGlobal("window", {sessionStorage: {
    get length() {return stored.size;}, key: (index: number) => [...stored.keys()][index] ?? null,
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value), removeItem: (key: string) => stored.delete(key)
  }});
  resetOutgoingImageReservationsForTest();
  try {
    const message = mintOutgoingMessage({content:[{piece:"text",text:"exact words"}], senderLabel:"owner", mode:"queue", pickedModel:"opus", pickedReasoningEffort:null, effortIsSet:true, pickedBackend:"claude"});
    const old = mintOutgoingMessage({content:[{piece:"text",text:"old words"}], senderLabel:"owner", mode:"queue"});
    await rememberOutgoingMessages("canonical-retry", [message, old]);
    const [reopened, historical] = await recallOutgoingMessages("canonical-retry");
    expect(reopened).toMatchObject({pickedModel:"opus",pickedReasoningEffort:null,effortIsSet:true,pickedBackend:"claude",knownFate:"sent_before_this_page"});
    const body = sendBodyFor({message:reopened, current:{model:"sonnet",reasoningEffort:"high"}, picked:{model:reopened.pickedModel ?? null, reasoningEffort:reopened.pickedReasoningEffort ?? null, effortIsSet:reopened.effortIsSet, backendKey:reopened.pickedBackend}, conversationId:null});
    expect(body).toMatchObject({model:"opus",reasoning_effort:null,backend_key:"claude",sender_message_id:message.messageId});
    const oldBody = sendBodyFor({message:historical,current:{model:"opus",reasoningEffort:"high"},picked:{model:null,reasoningEffort:null},conversationId:"c"});
    expect(oldBody).not.toHaveProperty("reasoning_effort");
  } finally {resetOutgoingImageReservationsForTest();vi.unstubAllGlobals();}
});
