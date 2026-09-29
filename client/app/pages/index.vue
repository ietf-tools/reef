<script setup lang="ts">
const {
  public: { rfceditorBase }
} = useRuntimeConfig()

await navigateTo(rfceditorBase, { external: true, redirectCode: 301 })
</script>
