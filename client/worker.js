// Everything the Worker route swallows that Django, not the static bundle, has
// to answer. Only the paths listed in `assets.run_worker_first` reach this
// handler at all — the rest of the site is served straight from ./dist without
// invoking any code — so there is nothing to match on here.
//
// A same-zone fetch() cannot re-enter a Worker route, so this reaches the zone
// origin (the Cloudflare Tunnel) instead of looping back into this Worker. That
// holds as long as the `global_fetch_strictly_public` compatibility flag stays
// off, which is the default.
export default {
  fetch(request) {
    return fetch(request)
  }
}
