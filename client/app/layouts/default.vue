<template>
  <div class="flex flex-col min-h-[100vh]">
    <HeaderWrapper class="border-b border-b-white dark:border-b-blue-900">
      <Header />
    </HeaderWrapper>
    <Main>
      <slot />
    </Main>
    <Footer />
  </div>
</template>

<style>
html {
  font-family: 'Inter', sans-serif;
}
</style>
