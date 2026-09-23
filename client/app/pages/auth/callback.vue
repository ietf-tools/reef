<script setup lang="ts">
const oidc = useOidc()
const { refresh } = useOidcSession()

onMounted(async () => {
  try {
    const user = await oidc.completeLogin()
    await refresh()
    const returnTo = (user?.state as { returnTo?: string } | undefined)?.returnTo
    await navigateTo(returnTo || '/')
  } catch {
    await navigateTo('/')
  }
})
</script>

<template>
  <div class="mx-auto max-w-md p-6 text-gray-600 dark:text-gray-300">Signing you in…</div>
</template>
