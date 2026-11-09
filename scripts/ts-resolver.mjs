import path from 'node:path';

/** Let plain `node` load the site's extensionless TypeScript imports. */
export async function resolve(specifier, context, nextResolve) {
  if ((specifier.startsWith('./') || specifier.startsWith('../')) && path.extname(specifier) === '') {
    return nextResolve(`${specifier}.ts`, context);
  }
  return nextResolve(specifier, context);
}
