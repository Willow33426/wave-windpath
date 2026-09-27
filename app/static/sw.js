self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
            const existing = windows.find((client) => new URL(client.url).origin === self.location.origin);
            return existing ? existing.focus() : clients.openWindow("/");
        }),
    );
});
