import { GraphicsBustInSilhouette } from '#components'
import { htmlEscapeToText } from '~/utilities/html'
import { useAuthStore } from '~/stores/auth'
import type { VueClick, VueStyleClass } from '~/utilities/vue'

/**
 * Although this type is recursive the UI only renders about 2 levels deep
 */
export type MenuItem = {
  icon?: () => VNode
  label: string
  description?: string
  hideMobile?: boolean
  hideDesktop?: boolean
  hideLabelDesktop?: boolean
  hideDropdownIconDesktop?: boolean
  highlightLink?: boolean
  hideNewWindowIcon?: boolean
  desktopClass?: VueStyleClass
  noSpaLink?: boolean
  href?: string
  click?: VueClick
  isActiveFn?: () => boolean
  role?: 'radiogroup' | 'radio'
  /**
   * The value a `radio` child contributes to its group's model
   */
  fieldValue?: string
  /**
   * Writable model for a `radiogroup`: the currently selected `fieldValue`
   */
  radioGroupRef?: Ref<string>
  activeLabelFn?: () => string
  children?: MenuItem[]
}

export const colorPreferences = [
  { value: 'system', label: 'System default' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' }
]

type Mode = 'desktop' | 'mobile'

export const groupLabelDomId = (mode: Mode, ...indexes: number[]): string => `${mode}-group-label-${indexes.join('-')}`

export const dropdownHeadingDomId = (mode: Mode, ...indexes: number[]): string =>
  `${mode}-dropdown-heading-label-${indexes.join('-')}`

export const descriptionDomId = (mode: Mode, ...indexes: number[]): string => `${mode}-description-${indexes.join('-')}`

export const useMenuData = (mode: Mode) => {
  const colorMode = useColorMode()
  const authStore = useAuthStore()
  const { isAuthenticated, user } = storeToRefs(authStore)
  const oidc = useOidc()

  // Writable model for the theme radio group. The selected value is the
  // colour-mode *preference* (e.g. 'system'), not the resolved value.
  const themeRef = computed<string>({
    get: () => colorMode.preference,
    set: (value) => {
      colorMode.preference = value || 'system'
    }
  })

  const menuData = computed(() => {
    const data: MenuItem[] = []

    const themeChildren: MenuItem[] = [
      {
        label: 'Theme',
        role: 'radiogroup',
        radioGroupRef: themeRef,
        children: colorPreferences.map(
          (colorPreference): MenuItem => ({
            label: colorPreference.label,
            activeLabelFn: () =>
              colorMode.preference === colorPreference.value
                ? `Selected ${colorPreference.label}`
                : `Not selected ${colorPreference.label}`,
            role: 'radio',
            fieldValue: colorPreference.value
          })
        )
      }
    ]

    if (isAuthenticated.value) {
      const displayName = user.value?.name ?? user.value?.preferredUsername ?? 'Account'
      const picture = user.value?.picture
      data.push({
        label: displayName,
        hideLabelDesktop: true,
        icon: picture
          ? () =>
              h('img', {
                src: picture,
                alt: `Picture of ${displayName}`,
                class: 'w-6 h-6 rounded-full'
              })
          : () =>
              h(GraphicsBustInSilhouette, {
                'aria-label': `${displayName}`,
                class: 'w-6 h-6 rounded-full'
              }),
        children: [
          {
            label: 'Sign out',
            click: () => {
              void oidc.logout()
            }
          },
          ...themeChildren
        ]
      })
    } else {
      data.push({
        label: 'User menu',
        hideLabelDesktop: true,
        icon: () => h(GraphicsBustInSilhouette, { class: 'w-6 h-6 rounded-full' }),
        children: [
          {
            label: 'Sign in',
            click: () => {
              void oidc.login()
            },
            hideNewWindowIcon: true
          },
          ...themeChildren
        ]
      })
    }

    return data.filter((item) => {
      // note: only a shallow filter, not deep
      if (mode === 'desktop' && item.hideDesktop) {
        return false
      }
      if (mode === 'mobile' && item.hideMobile) {
        return false
      }
      return true
    })
  })

  return menuData
}

type RenderNoScriptMenuItemOptions = {
  renderListDisc?: boolean
  menuHeaderTopSpacing?: boolean
}

/**
 * This generates raw HTML. It's uses our trusted menu data but be very careful making change regardless.
 */
export const renderNoScriptMenuItem = (menuItem: MenuItem, options?: RenderNoScriptMenuItemOptions): string => {
  if (menuItem.href) {
    return `<li class="${options?.renderListDisc ? 'list-disc ml-5' : ''}"><a href="${htmlEscapeToText(menuItem.href)}">${htmlEscapeToText(menuItem.label)}</a>${
      menuItem.children
        ? `<ul>${menuItem.children.map((menuItem) => renderNoScriptMenuItem(menuItem, options)).join('')}</ul>`
        : ''
    }</li>`
  }

  if (
    // NoScript users can't run click handler JS. Ignore this menu item.
    menuItem.click
  ) {
    return ''
  }

  if (menuItem.label && menuItem.children && menuItem.children.filter((menuItem) => !menuItem.click).length > 0) {
    return `<li>${
      menuItem.label
        ? `<b class="${options?.menuHeaderTopSpacing ? 'inline-block mt-1' : ''}">${htmlEscapeToText(menuItem.label)}</b>`
        : ''
    }${`<ul>${menuItem.children ? menuItem.children.map((menuItem) => renderNoScriptMenuItem(menuItem, options)).join('') : ''}</ul>`}</li>`
  }

  return ''
}
