import type { PromptDeliveryMode } from "../../../lib/conversation/wire";

/** The delivery choices while a turn runs. Each is its label alone on screen; what it does to
 *  the running turn is said to assistive technology and in the option's tooltip. */
export const DELIVERY_MODE_CHOICES: readonly {
  value: PromptDeliveryMode;
  name: string;
  meaning: string;
}[] = [
  { value: "steer", name: "Steer", meaning: "Add it to the running turn" },
  { value: "queue", name: "Queue", meaning: "Send it when this turn ends" },
  { value: "send_now", name: "Send now", meaning: "Stop this turn and run it next" }
];

export function deliveryModeChoices(modes: readonly PromptDeliveryMode[]) {
  return DELIVERY_MODE_CHOICES.filter((choice) => modes.includes(choice.value));
}
