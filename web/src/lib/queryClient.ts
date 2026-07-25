import { QueryClient } from "@tanstack/svelte-query";

// The single query client for the whole browser app. Every screen's query and
// the change stream's invalidation go through this one instance, so a change
// signal reaches exactly the screens that are on screen right now.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Only the parts of a response that actually changed become new objects,
      // so an unchanged card does not re-render after a refetch.
      structuralSharing: true,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
      // Server state is never assumed fresh: an invalidation always refetches.
      staleTime: 0,
      // A failed read surfaces immediately instead of being retried silently.
      retry: false
    }
  }
});
