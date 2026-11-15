/** A browser operation owns admission until ordinary demand shares its request. */
export type ViewPageRequestOwner = Readonly<{
    kind: 'restoration' | 'restoration-fallback';
    signal: AbortSignal;
    isCurrent: () => boolean;
}>;
