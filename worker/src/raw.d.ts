/**
 * Vite's `?raw` suffix, which hands a file's contents to the importer as a
 * string. Used by the tests to read config that is not TypeScript.
 */
declare module '*?raw' {
  const contents: string
  export default contents
}
