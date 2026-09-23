/**
 * Vue's `class=""` attribute can take strings, arrays, objects, arrays of objects, etc
 * but the typing is just `any`. Use this for `class` props.
 *
 * Named VueStyleClass rather than VueClass to distinguish from class-based components
 **/
export type VueStyleClass = string | Record<string, boolean | undefined> | VueStyleClass[]

export type VueClick = (event: Event) => void
