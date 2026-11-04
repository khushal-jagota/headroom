import { PlannerFetchError } from '../api';

/** An ordinary failed read, distinct from internal protocol and recovery state. */
export class NavigationRequestError extends PlannerFetchError {}
