import { lazy, type ComponentType, type LazyExoticComponent } from 'react'

export function lazyDefault<T extends ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
): LazyExoticComponent<T> {
  return lazy(factory)
}

export function lazyNamed(
  factory: () => Promise<Record<string, unknown>>,
  exportName: string,
): LazyExoticComponent<ComponentType<any>> {
  return lazy(async () => {
    const mod = await factory()
    const exported = mod[exportName]
    if (!exported) {
      throw new Error(`Missing export "${exportName}" in lazy module`)
    }
    return { default: exported as ComponentType<any> }
  })
}
