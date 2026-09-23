<template>
  <header class="flex-1 relative print:hidden">
    <a id="top"></a>
    <HeaderSkipToContent />
    <div>
      <div class="container pl-5 pr-3 mx-auto flex justify-between py-4 w-full">
        <GraphicsHeaderLogos />
        <nav aria-label="Primary" class="flex flex-1 flex-row justify-end content-end">
          <h2 class="sr-only">Primary navigation</h2>
          <HeaderNavMobile />
          <HeaderNavDesktop />
        </nav>
      </div>
      <HeaderNavMobileNoScript />
    </div>
    <!-- optional slot used to extend the header.
         per page content should still be in the <main> -->
    <slot />
  </header>
</template>

<script setup lang="ts">
useOidcSession()
</script>
