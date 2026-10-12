/**
 * Red serves the survey runner at `/survey/`, but links to `/s?slug=<slug>` on
 * this origin are still out there, in old mail and saved links, so each is sent
 * to Red's runner with the same query. Permanent, since the page is never served
 * here.
 */
export const redirectToRunner = (request: Request, env: Pick<Env, 'RED_ORIGIN'>): Response => {
  const { search } = new URL(request.url)
  return Response.redirect(`${env.RED_ORIGIN}/survey/${search}`, 301)
}
