<template>
  <component :is="props.icon()" v-if="props.icon" class="w-[18px] h-[18px]" />
</template>

<script setup lang="ts">
import type { MenuItem } from './HeaderNavData'

type Props = {
  icon?: MenuItem['icon']
}

const props = defineProps<Props>()
</script>
