import { PreviewError, type TicketEligibility } from '../contracts.ts';
export function require_vps(ticket: TicketEligibility): void {
  if (ticket.locale === 'macos')
    throw new PreviewError('Application previews are unavailable for Tickets running on the Mac.');
}
export function require_eligible(ticket: TicketEligibility): void {
  require_vps(ticket);
  if (ticket.terminal)
    throw new PreviewError('This Ticket has finished; its preview has expired.', 410);
  if (ticket.project_folder === null)
    throw new PreviewError('The Ticket project needs a configured folder.');
}
export function require_port(port: number, panels_port: number): void {
  if (port < 1024 || port > 65535 || [8767, panels_port].includes(port))
    throw new PreviewError('Choose a non-privileged application port distinct from Panels.');
}
