using System;
using System.IO.Pipes;
using System.Text;

public static class AskPass
{
    public static int Main(string[] args)
    {
        string target = Environment.GetEnvironmentVariable("TILEXR_SSH_PROMPT_TARGET");
        if (args.Length != 1 || String.IsNullOrEmpty(target) ||
            args[0].IndexOf(target, StringComparison.OrdinalIgnoreCase) < 0 ||
            args[0].IndexOf("password", StringComparison.OrdinalIgnoreCase) < 0)
            return 1;
        string name = Environment.GetEnvironmentVariable("TILEXR_SSH_PROMPT_PIPE");
        using (var pipe = new NamedPipeClientStream(".", name, PipeDirection.InOut))
        {
            pipe.Connect(4000);
            var bytes = new byte[1024];
            int size = pipe.Read(bytes, 0, bytes.Length);
            if (size == 0 || size == bytes.Length)
                return 1;
            Console.WriteLine(Encoding.UTF8.GetString(bytes, 0, size));
            Array.Clear(bytes, 0, size);
        }
        return 0;
    }
}
