import type { FastifyInstance, FastifyPluginAsync, FastifyRequest } from "fastify";
import type { Database } from "../database/index.ts";
import type { Config } from "../config.ts";
import type { Clock } from "../clock.ts";
import { requestContext } from "./identity.ts";
import { requestAdmission } from "./intake.ts";
import type { AdmissionDependencies } from "./intake.ts";
import type { ConversationSystem } from '../../conversation/contracts.ts';
import type { SignalNavigationAuthority } from '../../conversation/publication/authority.ts';
export interface AreaServices {
    conversationSystem?: ConversationSystem;
    signalAuthority?: SignalNavigationAuthority;
    database: Database;
    config: Config;
    clock: Clock;
    identity: typeof requestContext;
}
export interface Area {
    prefix: string;
    routes: FastifyPluginAsync<AreaServices>;
    admission?: AdmissionDependencies;
}
/** Every ordinary area passes this door; executor transport has a separate boundary. */
export function registerArea(app: FastifyInstance, area: Area, services: Omit<AreaServices, "identity">): void {
    app.register(async (scope) => {
        scope.addHook("preHandler", async (request: FastifyRequest) => { services.database.read(sql => requestAdmission(sql, request, area.admission)); });
        await scope.register(area.routes, { ...services, identity: requestContext });
    }, { prefix: area.prefix });
}
