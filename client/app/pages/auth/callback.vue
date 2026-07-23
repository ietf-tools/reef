<script setup lang="ts">
const oidc = useOidc();

onMounted(async () => {
  try {
    const user = await oidc.completeLogin();
    const returnTo = (user?.state as { returnTo?: string } | undefined)?.returnTo;
    await navigateTo(returnTo || "/");
  } catch {
    await navigateTo("/");
  }
});
</script>

<template>
  <main class="mx-auto max-w-md p-6 text-gray-600">Signing you in…</main>
</template>
