import { defineCollection, z } from "astro:content";

const guides = defineCollection({
  type: "content",
  schema: z.object({
    title: z.string(),
    description: z.string(),
    date: z.coerce.date(),
    draft: z.boolean().optional()
  }),
});

const articles = defineCollection({
  type: "content",
  schema: z.object({
    title: z.string(),
    description: z.string(),
    date: z.coerce.date(),
    draft: z.boolean().optional(),
    demoURL: z.string().optional(),
    repoURL: z.string().optional(),
    // authors is a list of objects, which consists of name, url
    // example: [{ name: "John Doe", url: "https://example.com" }]
    authors: z.array(z.object({
      name: z.string(),
      url: z.string()
    })).optional(),
  }),
});

export const collections = { guides, articles };
